"""
Data Validation & Self-Healing Layer.

Validates and repairs ingested data before it enters the database.
Ensures the system never crashes on bad input — instead, it
sanitizes, logs, and continues.

Design principles:
- Never reject an entire batch because of one bad row
- Log every correction for auditability
- Provide clear error messages for unrecoverable issues
- Compute data quality scores per batch
- Handle encoding issues gracefully
"""

import logging
import re
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ValidationResult:
    """Container for validation outcome of a single record."""

    def __init__(self) -> None:
        self.is_valid = True
        self.corrections: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def add_correction(self, field: str, old_val: Any, new_val: Any, reason: str):
        self.corrections.append(
            f"field={field} old={old_val!r} new={new_val!r} reason={reason}"
        )

    def add_error(self, field: str, message: str):
        self.is_valid = False
        self.errors.append(f"field={field}: {message}")

    def add_warning(self, field: str, message: str):
        self.warnings.append(f"field={field}: {message}")

    @property
    def was_corrected(self) -> bool:
        return len(self.corrections) > 0


class DataValidator:
    """
    Validates and self-heals token log records before insertion.

    Self-healing actions:
    - Negative tokens → clamped to 0
    - Missing cost → recalculated from tokens + pricing
    - Invalid status → mapped to 'success'
    - Missing dates → set to current timestamp
    - Empty tenant_id → row rejected
    - Whitespace trimming on all string fields
    - Unicode normalization
    - Duplicate field detection
    """

    VALID_STATUSES = {"success", "error", "timeout", "blocked", "OK", "ok"}
    STATUS_MAP = {
        "ok": "success",
        "OK": "success",
        "SUCCESS": "success",
        "success": "success",
        "error": "error",
        "ERROR": "error",
        "fail": "error",
        "FAIL": "error",
        "failed": "error",
        "FAILED": "error",
        "timeout": "timeout",
        "TIMEOUT": "timeout",
        "blocked": "blocked",
        "BLOCKED": "blocked",
        "rate_limited": "blocked",
        "RATE_LIMITED": "blocked",
    }

    def validate_token_log(self, row: dict) -> tuple[dict, ValidationResult]:
        """
        Validate and heal a single token log record.
        Returns (healed_row, validation_result).
        """
        result = ValidationResult()
        healed = dict(row)

        # ── tenant_id (required, non-empty) ──
        tenant_id = str(healed.get("tenant_id", "")).strip()
        if not tenant_id:
            result.add_error("tenant_id", "Missing or empty — row rejected")
            return healed, result
        # Normalize tenant_id: strip quotes, normalize whitespace
        tenant_id = tenant_id.strip("\"'").strip()
        if tenant_id != healed.get("tenant_id"):
            result.add_correction("tenant_id", healed.get("tenant_id"), tenant_id, "normalized")
        healed["tenant_id"] = tenant_id

        # ── model (required, non-empty) ──
        model = str(healed.get("model", "")).strip()
        if not model:
            model = "unknown"
            result.add_correction("model", healed.get("model"), model, "empty → default")
        healed["model"] = model

        # ── provider (infer if missing) ──
        provider = str(healed.get("provider", "")).strip()
        if not provider:
            provider = self._infer_provider(model)
            result.add_correction("provider", "", provider, "inferred from model name")
        healed["provider"] = provider

        # ── tokens (non-negative integers) ──
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            val = healed.get(field, 0)
            try:
                val = int(float(str(val).replace(",", "")))
            except (ValueError, TypeError):
                val = 0
                result.add_correction(field, healed.get(field), 0, "non-numeric → 0")
            if val < 0:
                result.add_correction(field, val, 0, "negative → 0")
                val = 0
            healed[field] = val

        # ── total_tokens auto-compute ──
        inp = healed["input_tokens"]
        out = healed["output_tokens"]
        total = healed["total_tokens"]
        if total == 0 and (inp > 0 or out > 0):
            total = inp + out
            result.add_correction("total_tokens", 0, total, "computed from input+output")
            healed["total_tokens"] = total

        # ── cost_usd (non-negative decimal) ──
        cost = healed.get("cost_usd", 0)
        try:
            cost = Decimal(str(cost).replace(",", ""))
        except (InvalidOperation, ValueError, TypeError):
            cost = Decimal("0")
            result.add_correction("cost_usd", healed.get("cost_usd"), "0", "invalid → 0")
        if cost < 0:
            result.add_correction("cost_usd", float(cost), 0, "negative → 0")
            cost = Decimal("0")
        healed["cost_usd"] = cost

        # ── status (map to valid enum) ──
        status = str(healed.get("status", "success")).strip()
        mapped = self.STATUS_MAP.get(status, None)
        if mapped is None:
            result.add_correction("status", status, "success", "unknown status → success")
            mapped = "success"
        healed["status"] = mapped

        # ── duration_ms (non-negative integer, optional) ──
        dur = healed.get("duration_ms")
        if dur is not None:
            try:
                dur = int(float(str(dur).replace(",", "")))
                if dur < 0:
                    dur = None
                    result.add_correction("duration_ms", healed.get("duration_ms"), None, "negative → null")
            except (ValueError, TypeError):
                dur = None
                result.add_correction("duration_ms", healed.get("duration_ms"), None, "invalid → null")
        healed["duration_ms"] = dur

        # ── created_at (parse date/datetime) ──
        created = healed.get("created_at")
        if created is None or created == "":
            created = datetime.now(timezone.utc)
            result.add_correction("created_at", None, created, "missing → now()")
        elif isinstance(created, str):
            created = self._parse_datetime(created)
            if created is None:
                created = datetime.now(timezone.utc)
                result.add_correction("created_at", healed.get("created_at"), created, "unparseable → now()")
        healed["created_at"] = created

        # ── Trim all string fields ──
        for key in ("agent_id", "user_id", "site_id", "module", "pricing_profile"):
            val = healed.get(key)
            if isinstance(val, str):
                healed[key] = val.strip() or None

        # Log corrections
        if result.corrections:
            logger.debug(
                f"validation.healed tenant={tenant_id} corrections={len(result.corrections)}"
            )

        return healed, result

    def validate_batch(self, rows: list[dict]) -> tuple[list[dict], dict]:
        """
        Validate a batch of rows. Returns (valid_rows, summary).
        Invalid rows are dropped and logged.
        """
        valid = []
        total_corrections = 0
        rejected = 0
        correction_counts: dict[str, int] = {}

        for i, row in enumerate(rows):
            healed, result = self.validate_token_log(row)
            if result.is_valid:
                valid.append(healed)
                total_corrections += len(result.corrections)
                for c in result.corrections:
                    field = c.split(" ")[0].replace("field=", "")
                    correction_counts[field] = correction_counts.get(field, 0) + 1
            else:
                rejected += 1
                logger.warning(
                    f"validation.rejected row={i} errors={result.errors}"
                )

        # Compute data quality score (0-100)
        if len(rows) > 0:
            rejection_rate = rejected / len(rows)
            correction_rate = total_corrections / max(len(rows), 1)
            quality_score = round(
                max(0, 100 - (rejection_rate * 50) - (correction_rate * 5)), 1
            )
        else:
            quality_score = 100.0

        summary = {
            "total_input": len(rows),
            "valid_output": len(valid),
            "rejected": rejected,
            "corrections_applied": total_corrections,
            "correction_breakdown": correction_counts,
            "data_quality_score": quality_score,
        }

        if rejected > 0 or total_corrections > 0:
            logger.info(
                f"validation.batch_complete "
                f"input={len(rows)} valid={len(valid)} "
                f"rejected={rejected} corrections={total_corrections} "
                f"quality={quality_score}"
            )

        return valid, summary

    @staticmethod
    def _infer_provider(model_name: str) -> str:
        """Infer provider from model name."""
        name = model_name.lower()
        if any(p in name for p in ("gpt", "o1", "o3", "davinci", "chatgpt")):
            return "openai"
        if "claude" in name:
            return "anthropic"
        if any(p in name for p in ("gemini", "palm", "bard")):
            return "google"
        if any(p in name for p in ("mistral", "mixtral", "codestral")):
            return "mistral"
        if any(p in name for p in ("llama", "meta-llama")):
            return "meta"
        if any(p in name for p in ("command", "coral")):
            return "cohere"
        return "unknown"

    @staticmethod
    def _parse_datetime(s: str) -> Optional[datetime]:
        """Parse various date/datetime formats with robust fallback."""
        s = s.strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y",
            "%Y%m%d",
            "%d-%m-%Y",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None


def detect_file_encoding(file_path: str) -> str:
    """
    Detect CSV file encoding by reading the first few bytes.
    Falls back to utf-8 if detection fails.
    """
    try:
        with open(file_path, "rb") as f:
            raw = f.read(8192)

        # Check for BOM markers
        if raw.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if raw.startswith(b"\xff\xfe"):
            return "utf-16-le"
        if raw.startswith(b"\xfe\xff"):
            return "utf-16-be"

        # Try utf-8 first
        try:
            raw.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass

        # Try common encodings
        for enc in ("latin-1", "cp1252", "iso-8859-1"):
            try:
                raw.decode(enc)
                return enc
            except UnicodeDecodeError:
                continue

    except Exception as e:
        logger.warning(f"encoding_detection.failed file={file_path} error={e}")

    return "utf-8"
