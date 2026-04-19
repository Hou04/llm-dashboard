"""
MLDetectionEngine — three-algorithm anomaly detection ensemble.

ALGORITHM 1: STL RESIDUAL Z-SCORE
    STL (Seasonal-Trend decomposition using Loess) splits a time series into:
        signal = trend + seasonal + residual
    The 'trend' is the long-term direction. The 'seasonal' component is the
    repeating weekly pattern. The 'residual' is what's left — pure noise plus
    genuine anomalies.

    We fit STL on the last 30 days of daily token totals, extract the residual,
    compute a Z-score on the residual for today's value. This means Monday is
    always compared to other Mondays, not to the overall mean. Weekend lows
    never fire alerts. Only genuine deviations from the seasonal pattern fire.

    Threshold: |Z| >= 2.5 on residual → anomaly
    Why 2.5 and not 2.0? Seasonal decomposition already removes the main
    sources of variance. The residual is cleaner, so the threshold can be
    tighter without increasing false positive rate.

ALGORITHM 2: ISOLATION FOREST
    Isolation Forest is an unsupervised ML algorithm based on random trees.
    Core insight: anomalies are rare and different. They are easy to isolate
    with random cuts. Normal points require many cuts to isolate.

    We build a 5-dimensional feature vector per day:
        [total_tokens, call_count, avg_tokens_per_call, cost_usd, error_rate]

    Train IsolationForest on the last 30 days. Score today.
    Score < threshold → anomaly.

    Why IsolationForest for multi-dimensional data?
    A token spike with normal call count is different from a token spike
    caused by more calls. Isolation Forest catches combinations that no
    single-dimension detector would catch — e.g., call_count normal but
    avg_tokens_per_call is 4x normal (someone sending massive prompts).

    Contamination: set to 0.05 (expect ~5% of days to be anomalies).
    This matches reality — roughly 1-2 genuinely unusual days per month.

ALGORITHM 3: CUSUM (Cumulative Sum Control Chart)
    CUSUM is from industrial process control (1954, E.S. Page). It detects
    persistent shifts in the process mean — changes that happen gradually
    over several observations.

    Standard CUSUM uses two accumulators:
        C_pos[t] = max(0, C_pos[t-1] + (x[t] - μ - k))  ← detects upward drift
        C_neg[t] = max(0, C_neg[t-1] - (x[t] - μ + k))  ← detects downward drift

    where:
        x[t] = today's normalized value (z-score of raw value)
        μ    = 0 (we use normalized values, so mean is zero)
        k    = slack parameter = 0.5 (standard choice, absorbs natural noise)
        h    = decision threshold = 4.0 (standard choice for 5-sigma process)

    When C_pos > h or C_neg > h: signal a persistent shift.

    Why does this catch what Z-score misses?
    If usage increases by 1.5σ for 5 consecutive days, Z-score sees
    "warning" each day but never "high" or "critical". CUSUM accumulates:
    after day 5: C_pos ≈ 5 × (1.5 - 0.5) = 5.0 > 4.0 → anomaly.
    The slow ramp-up from a new feature or growing user base is caught.

ENSEMBLE VOTING:
    Each algorithm outputs a binary vote (anomaly / not anomaly).
    Final decision:
        0 votes → normal
        1 vote  → weak signal, only record if very high confidence
        2 votes → warning or high (most common real anomaly)
        3 votes → critical (very high confidence)

    Severity also scales with the magnitude of the highest individual score.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from statsmodels.tsa.seasonal import STL

logger = logging.getLogger(__name__)

# ============================================================
# THRESHOLDS — tuned for daily LLM usage data
# ============================================================

STL_Z_THRESHOLD = 2.5       # STL residual Z-score to fire
ISO_SCORE_THRESHOLD = -0.1  # IsolationForest score below this = anomaly
CUSUM_H = 4.0               # CUSUM decision threshold (standard: 4-5)
CUSUM_K = 0.5               # CUSUM slack parameter (standard: 0.5)
MIN_SAMPLE_DAYS = 14        # Minimum days of data before any detector activates


# ============================================================
# RESULT OBJECTS
# ============================================================

@dataclass
class DetectorVote:
    """Result from a single detector."""
    name: str          # "stl" / "isolation_forest" / "cusum"
    fired: bool
    score: float       # the raw score (for audit trail)
    direction: str     # "up" / "down" / "none"


@dataclass
class EnsembleResult:
    """Combined result from all three detectors."""
    fired: bool
    severity: str                    # normal / warning / high / critical
    anomaly_type: str                # token_spike / token_drop / pattern_break
    votes: list[DetectorVote]
    vote_count: int
    detector_names: list[str]        # names of detectors that voted

    # Individual scores for storage
    stl_residual_zscore: Optional[float] = None
    isolation_score: Optional[float] = None
    cusum_value: Optional[float] = None

    # Updated CUSUM state (must be persisted to baseline)
    new_cusum_pos: float = 0.0
    new_cusum_neg: float = 0.0

    @property
    def description_parts(self) -> list[str]:
        return [f"{v.name}(score={v.score:.2f})" for v in self.votes if v.fired]


# ============================================================
# THE ENGINE
# ============================================================

class MLDetectionEngine:
    """
    Stateless ML detection engine.

    All state (historical values, CUSUM accumulators) is passed in
    and returned — never stored inside this class. This makes it
    testable and safe to use in async contexts.

    Usage:
        engine = MLDetectionEngine()
        result = engine.detect(
            history=daily_token_values,    # list of floats, oldest first
            today_value=today_tokens,
            today_features=feature_vector,
            cusum_pos=baseline.cusum_pos,
            cusum_neg=baseline.cusum_neg,
            mean=baseline.daily_mean,
            std_dev=baseline.daily_std_dev,
        )
    """

    def detect(
        self,
        history: list[float],          # daily token totals, oldest-first, last 30 days
        today_value: float,            # today's running total
        today_features: list[float],   # [tokens, calls, avg_tokens, cost, error_rate]
        history_features: list[list[float]],  # same features for each history day
        cusum_pos: float,              # current CUSUM positive accumulator
        cusum_neg: float,              # current CUSUM negative accumulator
        mean: float,                   # baseline daily mean
        std_dev: float,                # baseline daily std dev
        isolation_threshold: float = ISO_SCORE_THRESHOLD,
    ) -> EnsembleResult:
        """
        Run all three detectors and return ensemble result.

        Args:
            history: List of daily token totals for the baseline period.
                     Must be ordered oldest-first. Min length: MIN_SAMPLE_DAYS.
            today_value: Today's current token total (may be partial day).
            today_features: Multi-dimensional feature vector for today.
            history_features: Same feature vector for each day in history.
            cusum_pos: Current positive CUSUM accumulator from baseline.
            cusum_neg: Current negative CUSUM accumulator from baseline.
            mean: Baseline daily mean tokens.
            std_dev: Baseline daily std dev tokens.
            isolation_threshold: Score threshold for IsolationForest.
        """
        if len(history) < MIN_SAMPLE_DAYS:
            return self._no_detection_result(cusum_pos, cusum_neg)

        votes: list[DetectorVote] = []

        # --- Run detector 1: STL ---
        stl_vote, stl_z = self._run_stl(history, today_value)
        votes.append(stl_vote)

        # --- Run detector 2: Isolation Forest ---
        iso_vote, iso_score = self._run_isolation_forest(
            history_features, today_features, isolation_threshold
        )
        votes.append(iso_vote)

        # --- Run detector 3: CUSUM ---
        cusum_vote, new_pos, new_neg, cusum_val = self._run_cusum(
            today_value, mean, std_dev, cusum_pos, cusum_neg
        )
        votes.append(cusum_vote)

        # --- Run detector 4: Changepoint ---
        cp_vote, cp_z = self._run_changepoint(history, today_value)
        votes.append(cp_vote)

        # --- Ensemble voting ---
        fired_votes = [v for v in votes if v.fired]
        vote_count = len(fired_votes)
        fired = vote_count >= 2  # majority vote

        # Determine direction from the votes that fired
        directions = [v.direction for v in fired_votes]
        direction = "up" if directions.count("up") > directions.count("down") else "down"

        # Severity: combination of vote count and magnitude
        severity = self._severity_from_votes(vote_count, stl_z, iso_score, cusum_val, cp_z)
        anomaly_type = self._anomaly_type(fired, direction, fired_votes)

        return EnsembleResult(
            fired=fired,
            severity=severity if fired else "normal",
            anomaly_type=anomaly_type,
            votes=votes,
            vote_count=vote_count,
            detector_names=[v.name for v in fired_votes],
            stl_residual_zscore=stl_z,
            isolation_score=iso_score,
            cusum_value=cusum_val,
            changepoint_zscore=cp_z,
            new_cusum_pos=new_pos,
            new_cusum_neg=new_neg,
        )

    # ============================================================
    # DETECTOR 1 — STL RESIDUAL Z-SCORE
    # ============================================================

    def _run_stl(
        self, history: list[float], today_value: float
    ) -> tuple[DetectorVote, Optional[float]]:
        """
        Fit STL on history + today, extract residual, compute Z-score.

        STL requires at least 2 full seasonal periods. With period=7 (weekly),
        we need at least 14 data points. We append today_value to history to
        score the most recent point.
        """
        try:
            series = np.array(history + [today_value], dtype=float)

            # Replace zeros with small values (log-transform needs positives)
            series = np.where(series <= 0, 1.0, series)

            # STL with period=7 (weekly seasonality)
            # seasonal=7: number of Loess smoothing points for seasonal component
            # trend=15: larger window for trend (captures monthly drift without
            #           being too sensitive to week-to-week noise)
            stl = STL(series, period=7, seasonal=7, trend=15, robust=True)
            result = stl.fit()

            residuals = result.resid
            residual_mean = np.mean(residuals[:-1])  # stats from history only
            residual_std = np.std(residuals[:-1], ddof=1)

            if residual_std < 1.0:
                residual_std = max(residual_std, np.mean(np.abs(residuals[:-1])) * 0.1 + 1)

            today_residual = residuals[-1]
            z = float((today_residual - residual_mean) / residual_std)
            fired = abs(z) >= STL_Z_THRESHOLD
            direction = "up" if z > 0 else "down"

            return DetectorVote("stl", fired, round(z, 3), direction), round(z, 3)

        except Exception as exc:
            logger.warning(f"STL detector failed: {exc}")
            return DetectorVote("stl", False, 0.0, "none"), None

    # ============================================================
    # DETECTOR 2 — ISOLATION FOREST
    # ============================================================

    def _run_isolation_forest(
        self,
        history_features: list[list[float]],
        today_features: list[float],
        threshold: float,
    ) -> tuple[DetectorVote, Optional[float]]:
        """
        Train IsolationForest on history, score today's feature vector.

        IsolationForest.score_samples() returns anomaly scores where:
            score near 0  → normal
            score << 0    → anomaly (easy to isolate)

        We use score_samples (not predict) because we want the continuous
        score, not just binary -1/+1. This lets us tune the threshold and
        store the magnitude.
        """
        try:
            if len(history_features) < MIN_SAMPLE_DAYS:
                return DetectorVote("isolation_forest", False, 0.0, "none"), None

            X_train = np.array(history_features, dtype=float)
            x_today = np.array([today_features], dtype=float)

            # Replace NaN/Inf with 0
            X_train = np.nan_to_num(X_train)
            x_today = np.nan_to_num(x_today)

            # contamination=0.05: we expect ~5% of days to be anomalies
            # random_state=42: reproducible results across calls
            # n_estimators=100: standard, good balance of speed vs accuracy
            clf = IsolationForest(
                contamination=0.05,
                n_estimators=100,
                random_state=42,
            )
            clf.fit(X_train)

            score = float(clf.score_samples(x_today)[0])
            fired = score < threshold
            # IsolationForest doesn't give direction — use token delta for that
            token_idx = 0
            direction = "up" if today_features[token_idx] > np.mean(X_train[:, token_idx]) else "down"

            return DetectorVote("isolation_forest", fired, round(score, 4), direction), round(score, 4)

        except Exception as exc:
            logger.warning(f"IsolationForest detector failed: {exc}")
            return DetectorVote("isolation_forest", False, 0.0, "none"), None

    # ============================================================
    # DETECTOR 3 — CUSUM
    # ============================================================

    def _run_cusum(
        self,
        today_value: float,
        mean: float,
        std_dev: float,
        cusum_pos: float,
        cusum_neg: float,
    ) -> tuple[DetectorVote, float, float, float]:
        """
        Update CUSUM accumulators with today's value.

        We normalize today_value to a Z-score first so the CUSUM
        thresholds (h=4.0, k=0.5) are model-independent.

        Returns (vote, new_cusum_pos, new_cusum_neg, cusum_signal_value).
        The signal value is max(cusum_pos, cusum_neg) — used for severity.
        """
        effective_std = max(std_dev, mean * 0.05 + 0.001)
        z_today = (today_value - mean) / effective_std

        # CUSUM update equations (Page, 1954)
        new_pos = max(0.0, cusum_pos + z_today - CUSUM_K)
        new_neg = max(0.0, cusum_neg - z_today - CUSUM_K)

        cusum_val = max(new_pos, new_neg)
        fired = cusum_val >= CUSUM_H

        direction = "up" if new_pos > new_neg else "down"

        return (
            DetectorVote("cusum", fired, round(cusum_val, 3), direction),
            new_pos,
            new_neg,
            round(cusum_val, 3),
        )

    # ============================================================
    # DETECTOR 4 — CHANGEPOINT
    # ============================================================

    def _run_changepoint(
        self,
        history: list[float],
        today_value: float,
    ) -> tuple[DetectorVote, Optional[float]]:
        """
        Detect structural breaks by comparing the mean of the most recent 7 days
        (including today) to the mean of the preceding window (up to 14 days prior).
        """
        if len(history) < 14:
            return DetectorVote("changepoint", False, 0.0, "none"), None
            
        recent_window = history[-6:] + [today_value]
        baseline_window = history[:-6]
        
        recent_mean = float(np.mean(recent_window))
        baseline_mean = float(np.mean(baseline_window))
        baseline_std = float(np.std(baseline_window, ddof=1)) if len(baseline_window) > 1 else 0.0
        
        if baseline_std < 1.0:
            baseline_std = max(baseline_std, baseline_mean * 0.1 + 1.0)
            
        z_shift = (recent_mean - baseline_mean) / baseline_std
        
        fired = abs(z_shift) >= 3.0  # Require a 3-sigma shift over a weekly average
        direction = "up" if z_shift > 0 else "down"
        
        return DetectorVote("changepoint", fired, round(z_shift, 3), direction), round(z_shift, 3)

    # ============================================================
    # SEVERITY AND TYPE MAPPING
    # ============================================================

    def _severity_from_votes(
        self,
        vote_count: int,
        stl_z: Optional[float],
        iso_score: Optional[float],
        cusum_val: Optional[float],
        cp_z: Optional[float],
    ) -> str:
        """
        Map vote count and individual scores to a severity level across 4 detectors.
        """
        if vote_count < 2:
            return "normal"

        magnitude = 0.0
        if stl_z is not None:
            magnitude = max(magnitude, abs(stl_z) / STL_Z_THRESHOLD)
        if cusum_val is not None:
            magnitude = max(magnitude, cusum_val / CUSUM_H)
        if iso_score is not None:
            magnitude = max(magnitude, abs(iso_score) / abs(ISO_SCORE_THRESHOLD))
        if cp_z is not None:
            magnitude = max(magnitude, abs(cp_z) / 3.0)

        if vote_count >= 3:
            return "critical" if magnitude >= 3.0 else "high"
        else:  # vote_count == 2
            return "high" if magnitude >= 2.0 else "warning"

    def _anomaly_type(
        self,
        fired: bool,
        direction: str,
        fired_votes: list[DetectorVote],
    ) -> str:
        if not fired:
            return "none"

        iso_fired = any(v.name == "isolation_forest" for v in fired_votes)
        stl_fired = any(v.name == "stl" for v in fired_votes)
        cp_fired = any(v.name == "changepoint" for v in fired_votes)

        if iso_fired and not stl_fired and not cp_fired:
            return "pattern_break"
            
        if cp_fired:
            return "regime_change"

        return "token_spike" if direction == "up" else "token_drop"

    def _no_detection_result(
        self, cusum_pos: float, cusum_neg: float
    ) -> EnsembleResult:
        return EnsembleResult(
            fired=False,
            severity="normal",
            anomaly_type="none",
            votes=[],
            vote_count=0,
            detector_names=[],
            new_cusum_pos=cusum_pos,
            new_cusum_neg=cusum_neg,
        )

    @staticmethod
    def build_feature_vector(
        tokens: float,
        calls: float,
        cost_usd: float,
        error_count: float,
        latency_ms: float = 0.0,
    ) -> list[float]:
        """
        Build the 6-dimensional feature vector for IsolationForest.
        Features:
          [0] total_tokens
          [1] call_count
          [2] avg_tokens_per_call
          [3] cost_usd * 100
          [4] error_rate_pct
          [5] latency_ms
        """
        avg_tokens = tokens / calls if calls > 0 else 0.0
        error_rate = (error_count / calls * 100) if calls > 0 else 0.0
        return [
            float(tokens),
            float(calls),
            float(avg_tokens),
            float(cost_usd) * 100,
            float(error_rate),
            float(latency_ms),
        ]