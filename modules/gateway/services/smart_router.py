"""
M7 Smart Router — ML-based cost-optimized model selection.

Uses LogisticRegression trained on historical call data with quality_score
labels to predict whether a cheaper model can handle a given prompt
with acceptable quality.

DESIGN DECISIONS:
- LogisticRegression is chosen for sub-5ms inference speed
- Model is re-trained periodically (not on every call)
- Falls back to the requested model if no training data exists
- Conservative: prefers false negatives (uses expensive model) over
  false positives (routes to cheap model with bad quality)

TRAINING DATA:
- Source: llm_token_log rows where quality_score IS NOT NULL
- Features: input_tokens, output_tokens, model_index (encoded)
- Target: 1 if quality_score >= threshold, 0 otherwise
- Minimum 50 labeled samples before the router activates

INFERENCE (<5ms):
- LogisticRegression.predict_proba() is essentially a matrix multiply
- No GPU required, runs on any CPU
- Feature vector is 3 floats: [input_tokens, output_tokens, model_index]
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

# Minimum quality score to consider a response "good enough"
QUALITY_THRESHOLD = 0.7

# Minimum labeled samples before the router activates
MIN_TRAINING_SAMPLES = 50

# Maximum age of the model before retraining (hours)
MODEL_MAX_AGE_HOURS = 6

# Model pricing tiers: cheaper models first
MODEL_COST_TIERS = [
    # (model_name, relative_cost)  — lower cost = preferred if quality is sufficient
    ("gpt-4o-mini", 0.15),
    ("claude-3-haiku-20240307", 0.25),
    ("gpt-3.5-turbo", 0.50),
    ("claude-3-sonnet-20240229", 1.0),
    ("gpt-4o", 1.5),
    ("gpt-4-turbo", 2.0),
    ("claude-3-opus-20240229", 3.0),
]

# Build lookup
_MODEL_COST = {m: c for m, c in MODEL_COST_TIERS}
_CHEAPER_MODELS = {m: [m2 for m2, c2 in MODEL_COST_TIERS if c2 < c] for m, c in MODEL_COST_TIERS}


class SmartRouter:
    """
    ML-based model router that predicts if a cheaper model can handle
    a given request with acceptable quality.

    Lifecycle:
    1. Initialized with no model (routing disabled)
    2. Trained on historical data via train()
    3. route() returns the cheapest viable model or None
    4. Re-trained periodically when model expires

    Thread-safety: the model is replaced atomically via reference swap.
    """

    def __init__(self):
        self._model: Optional[LogisticRegression] = None
        self._label_encoder: Optional[LabelEncoder] = None
        self._trained_at: Optional[datetime] = None
        self._sample_count: int = 0
        self._known_models: list[str] = []

    @property
    def is_ready(self) -> bool:
        """Check if the router has a trained model."""
        return self._model is not None and self._sample_count >= MIN_TRAINING_SAMPLES

    @property
    def is_stale(self) -> bool:
        """Check if the model needs retraining."""
        if self._trained_at is None:
            return True
        age_hours = (datetime.now(timezone.utc) - self._trained_at).total_seconds() / 3600
        return age_hours > MODEL_MAX_AGE_HOURS

    def train(self, training_data: list[dict]) -> dict:
        """
        Train the logistic regression model on historical call data.

        Args:
            training_data: List of dicts with keys:
                - model: str (model name)
                - input_tokens: int
                - output_tokens: int
                - quality_score: float (0.0-1.0)

        Returns:
            Training summary dict with accuracy and sample count.
        """
        if len(training_data) < MIN_TRAINING_SAMPLES:
            logger.info(
                f"SmartRouter: insufficient training data ({len(training_data)}/{MIN_TRAINING_SAMPLES})"
            )
            return {
                "status": "insufficient_data",
                "samples": len(training_data),
                "min_required": MIN_TRAINING_SAMPLES,
            }

        start = time.perf_counter()

        # Encode models
        models = [d["model"] for d in training_data]
        le = LabelEncoder()
        le.fit(list(set(models)))

        # Build feature matrix
        X = np.array([
            [
                d["input_tokens"],
                d["output_tokens"],
                le.transform([d["model"]])[0],
            ]
            for d in training_data
        ], dtype=float)

        # Binary target: 1 = quality is sufficient
        y = np.array([
            1 if d["quality_score"] >= QUALITY_THRESHOLD else 0
            for d in training_data
        ])

        # Check class balance
        if len(set(y)) < 2:
            logger.warning("SmartRouter: only one class in training data, skipping")
            return {
                "status": "single_class",
                "samples": len(training_data),
                "positive_rate": float(np.mean(y)),
            }

        # Train
        clf = LogisticRegression(
            max_iter=200,
            solver="lbfgs",
            class_weight="balanced",  # handle imbalanced quality labels
            random_state=42,
        )
        clf.fit(X, y)

        # Compute training accuracy
        accuracy = float(clf.score(X, y))
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Atomic swap
        self._model = clf
        self._label_encoder = le
        self._trained_at = datetime.now(timezone.utc)
        self._sample_count = len(training_data)
        self._known_models = list(le.classes_)

        logger.info(
            f"SmartRouter trained: {len(training_data)} samples, "
            f"accuracy={accuracy:.3f}, elapsed={elapsed_ms:.1f}ms"
        )

        return {
            "status": "trained",
            "samples": len(training_data),
            "accuracy": round(accuracy, 4),
            "training_time_ms": round(elapsed_ms, 1),
            "known_models": self._known_models,
        }

    def route(
        self,
        model_requested: str,
        input_tokens: int,
        output_tokens: int,
        min_confidence: float = 0.85,
    ) -> Optional[str]:
        """
        Predict the cheapest model that can handle this request with
        sufficient quality.

        Args:
            model_requested: The model originally requested.
            input_tokens: Number of input tokens.
            output_tokens: Expected output tokens (estimate).
            min_confidence: Minimum probability threshold for routing
                          (higher = more conservative). Default 0.85.

        Returns:
            The name of a cheaper model if one is predicted to work,
            or None if the requested model should be used.

        Performance: <5ms on any modern CPU.
        """
        if not self.is_ready:
            return None

        # Only route if the requested model is known and has cheaper alternatives
        if model_requested not in _CHEAPER_MODELS:
            return None

        cheaper_options = _CHEAPER_MODELS[model_requested]
        if not cheaper_options:
            return None  # already the cheapest

        start = time.perf_counter()

        # Try each cheaper model from cheapest to most expensive
        best_route = None
        for candidate in cheaper_options:
            if candidate not in self._known_models:
                continue

            # Build feature vector for the candidate
            try:
                model_idx = self._label_encoder.transform([candidate])[0]
            except ValueError:
                continue

            X = np.array([[input_tokens, output_tokens, model_idx]])

            # Predict probability of sufficient quality
            proba = self._model.predict_proba(X)[0]
            # Index 1 = probability of quality >= threshold
            prob_good = proba[1] if len(proba) > 1 else 0.0

            if prob_good >= min_confidence:
                best_route = candidate
                # Don't break — continue checking even cheaper models

        elapsed_ms = (time.perf_counter() - start) * 1000

        if best_route:
            logger.debug(
                f"SmartRouter: {model_requested} → {best_route} "
                f"(confidence={prob_good:.3f}, latency={elapsed_ms:.2f}ms)"
            )

        return best_route

    def get_status(self) -> dict:
        """Return current router status for monitoring."""
        return {
            "is_ready": self.is_ready,
            "is_stale": self.is_stale,
            "sample_count": self._sample_count,
            "min_required": MIN_TRAINING_SAMPLES,
            "trained_at": self._trained_at.isoformat() if self._trained_at else None,
            "known_models": self._known_models,
        }


# ================================================================
# MODULE-LEVEL SINGLETON
# ================================================================

_smart_router: Optional[SmartRouter] = None


def get_smart_router() -> SmartRouter:
    """Get or create the singleton SmartRouter instance."""
    global _smart_router
    if _smart_router is None:
        _smart_router = SmartRouter()
    return _smart_router
