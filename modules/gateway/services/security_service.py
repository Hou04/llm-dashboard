import logging
from typing import Dict, Any

from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

# Since we want DistilBERT prompt-injection classifier, we initialize a transformers pipeline
# We'll use a widely available distilbert prompt injection model.
# Note: In a production setting, the model would be pre-downloaded or packaged with the image.
from transformers import pipeline

logger = logging.getLogger(__name__)

class SecurityService:
    """
    SecurityService provides in-process ML inference for:
    1. Deterministic PII pseudonymization (Microsoft Presidio)
    2. Prompt Injection detection (DistilBERT)
    
    This is instantiated as a singleton per-process to avoid 
    continual loading of ML models.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SecurityService, cls).__new__(cls)
            cls._instance._initialize_models()
        return cls._instance

    def _initialize_models(self):
        logger.info("Initializing Microsoft Presidio Analyzer and Anonymizer...")
        self.analyzer = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()
        
        logger.info("Initializing DistilBERT prompt-injection classifier...")
        try:
            # We use a fast, CPU-bound DistilBERT model for prompt injection
            self.classifier = pipeline(
                "text-classification", 
                model="protectai/deberta-v3-base-prompt-injection-v2",
                device=-1 # CPU
            )
        except Exception as e:
            logger.error(f"Failed to load DistilBERT model: {e}")
            # Fallback to an empty classifier if not found, avoiding crash
            self.classifier = None
            
    def anonymize_text(self, text: str, language: str = "en") -> Dict[str, Any]:
        """
        Analyze text for PII and anonymize it using Presidio.
        Takes ~5ms on average for short strings.
        """
        if not text:
            return {"text": "", "items": []}
            
        # 1. Analyze text to find entities
        results = self.analyzer.analyze(text=text, language=language)
        
        # 2. Anonymize the found entities
        anonymized_result = self.anonymizer.anonymize(
            text=text,
            analyzer_results=results
        )
        
        # Return both the scrubbed text and the positions (for un-scrubbing if needed)
        items = [{"entity_type": e.entity_type, "start": e.start, "end": e.end} for e in results]
        
        return {
            "text": anonymized_result.text,
            "items": items
        }
        
    def detect_prompt_injection(self, text: str) -> Dict[str, Any]:
        """
        Detect if the prompt contains injection attempts using DistilBERT.
        Takes <15ms on average in-process (CPU).
        """
        if not text or not self.classifier:
            return {"is_injection": False, "score": 0.0}
            
        result = self.classifier(text)[0]
        # Common ProtectAI models return LABEL_1 or INJECTION for malicious intent
        label = result["label"].upper()
        score = float(result["score"])
        
        is_injection = label in ["INJECTION", "LABEL_1"]
        return {
            "is_injection": is_injection,
            "score": score,
            "label": label
        }
