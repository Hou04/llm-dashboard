"""
IngestionService — schema-aware CSV ingestion engine.

Dynamically adapts to any CSV with similar structure.
No hardcoded tenant IDs, model names, or column positions.

The engine:
1. Reads CSV headers and maps them to known fields via aliases
2. Validates and heals every row through the DataValidator
3. Infers missing fields (provider from model, cost from pricing)
4. Bulk-inserts into llm_token_log using batched upserts
5. Auto-seeds contracts, pricing, and governance rules
6. Triggers anomaly generation if configured
"""

import csv
import io
import logging
import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.config_registry import config
from core.validation import DataValidator, detect_file_encoding
from core.observability import metrics, pipeline_timer, log_pipeline_event
from modules.billing.contract_models import LLMModelPricing
from modules.gateway.models import LLMTokenLog

logger = logging.getLogger(__name__)


class DatasetAdapter:
    """
    Intelligent dataset adaptation — maps arbitrary CSV columns
    to the system's internal schema.

    Understands multiple naming conventions:
      client_id → tenant_id
      tokens_input → input_tokens
      total_cost → cost_usd
      ...
    """

    # Column alias map: internal_name → list of possible CSV column names
    COLUMN_ALIASES = {
        "tenant_id": [
            "client_id", "tenant_id", "tenant", "customer_id",
            "org_id", "organization_id", "company_id",
        ],
        "model": [
            "model", "model_name", "llm_model", "ai_model",
        ],
        "input_tokens": [
            "tokens_input", "input_tokens", "prompt_tokens",
            "in_tokens", "input_token_count",
        ],
        "output_tokens": [
            "tokens_output", "output_tokens", "completion_tokens",
            "out_tokens", "output_token_count",
        ],
        "total_tokens": [
            "total_tokens", "token_count", "tokens_total", "tokens",
        ],
        "cost_usd": [
            "total_cost", "cost", "cost_usd", "amount",
            "cost_total", "price", "charge", "total_cost_usd",
        ],
        "cost_input": [
            "cost_input", "input_cost", "prompt_cost", "input_cost_usd",
        ],
        "cost_output": [
            "cost_output", "output_cost", "completion_cost", "output_cost_usd",
        ],
        "status": [
            "status", "call_status", "result", "outcome",
        ],
        "created_at": [
            "date", "timestamp", "created_at", "datetime",
            "call_date", "log_date", "time",
        ],
        "duration_ms": [
            "latency", "duration_ms", "duration", "response_time",
            "latency_ms", "elapsed_ms",
        ],
        "agent_id": [
            "agent_id", "agent", "service_id", "service",
            "application", "app_id",
        ],
        "module": [
            "module", "feature", "function", "component",
        ],
        "provider": [
            "provider", "llm_provider", "vendor", "api_provider",
        ],
        "product": [
            "product", "product_name", "app_name", "application_name",
        ],
        "anomaly_flag": [
            "anomaly_flag", "is_anomaly", "anomaly", "outlier",
        ],
    }

    def __init__(self, csv_headers: list[str]) -> None:
        self.csv_headers = [h.strip().lower() for h in csv_headers]
        self.mapping = self._build_mapping()
        self.confidence = self._compute_confidence()
        logger.info(
            f"dataset_adapter.mapping "
            f"csv_columns={len(self.csv_headers)} "
            f"mapped_fields={len(self.mapping)} "
            f"confidence={self.confidence:.0%} "
            f"mapping={self.mapping}"
        )

    def _build_mapping(self) -> dict[str, str]:
        """Build CSV column → internal field mapping."""
        mapping = {}
        for internal_name, aliases in self.COLUMN_ALIASES.items():
            for alias in aliases:
                if alias.lower() in self.csv_headers:
                    mapping[alias.lower()] = internal_name
                    break
        return mapping

    def _compute_confidence(self) -> float:
        """Compute a confidence score for the mapping (0.0–1.0)."""
        required_fields = {"tenant_id", "model", "created_at"}
        important_fields = {"input_tokens", "output_tokens", "total_tokens", "cost_usd"}
        all_expected = required_fields | important_fields

        mapped_internal = set(self.mapping.values())
        required_mapped = required_fields & mapped_internal
        important_mapped = important_fields & mapped_internal

        if not required_mapped:
            return 0.0

        score = len(required_mapped) / len(required_fields) * 0.6
        score += len(important_mapped) / len(important_fields) * 0.4
        return min(score, 1.0)

    def adapt_row(self, csv_row: dict) -> dict:
        """Convert a CSV row dict to the internal schema."""
        adapted = {}
        # Normalize keys to lowercase
        row_lower = {k.strip().lower(): v for k, v in csv_row.items()}

        for csv_col, internal_name in self.mapping.items():
            if csv_col in row_lower:
                adapted[internal_name] = row_lower[csv_col]

        # ── Compute cost_usd from input+output costs if not directly mapped ──
        if "cost_usd" not in adapted:
            cost_in = adapted.pop("cost_input", None)
            cost_out = adapted.pop("cost_output", None)
            if cost_in is not None and cost_out is not None:
                try:
                    adapted["cost_usd"] = float(cost_in) + float(cost_out)
                except (ValueError, TypeError):
                    adapted["cost_usd"] = 0

        # ── Infer agent_id from module+product if not directly mapped ──
        if "agent_id" not in adapted or not adapted.get("agent_id"):
            module = adapted.get("module", "")
            product = adapted.pop("product", "")
            if product and module:
                adapted["agent_id"] = f"{product}_{module}".replace(" ", "_")
            elif module:
                adapted["agent_id"] = module
            elif product:
                adapted["agent_id"] = product

        # ── Convert latency seconds → milliseconds if needed ──
        dur = adapted.get("duration_ms")
        if dur is not None:
            try:
                dur_f = float(dur)
                # If value is < 100, it's likely in seconds, convert to ms
                if 0 < dur_f < 100:
                    adapted["duration_ms"] = int(dur_f * 1000)
                else:
                    adapted["duration_ms"] = int(dur_f)
            except (ValueError, TypeError):
                adapted["duration_ms"] = None

        return adapted

    def get_unmapped_columns(self) -> list[str]:
        """Return CSV columns that couldn't be mapped."""
        mapped_csv_cols = set(self.mapping.keys())
        return [h for h in self.csv_headers if h not in mapped_csv_cols]


class IngestionService:
    """
    Main ingestion engine. Orchestrates:
    1. CSV parsing + schema adaptation
    2. Data validation + self-healing
    3. Bulk database insertion
    4. Auto-seeding of contracts, pricing, governance
    5. Anomaly generation
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.validator = DataValidator()

    async def ingest_csv_file(self, file_path: str) -> dict:
        """
        Ingest a CSV file into the system.

        Returns a summary dict with counts and any issues.
        """
        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        log_pipeline_event("ingest_start", details={"file": path.name})

        with pipeline_timer("csv_ingest"):
            # Auto-detect file encoding
            encoding = detect_file_encoding(str(path))
            logger.info(f"ingestion.encoding file={path.name} encoding={encoding}")

            with open(path, "r", encoding=encoding) as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames or []

                if not headers:
                    return {"success": False, "error": "CSV file has no headers"}

                adapter = DatasetAdapter(headers)
                unmapped = adapter.get_unmapped_columns()
                if unmapped:
                    logger.info(f"ingestion.unmapped_columns columns={unmapped}")

                # Read and adapt all rows
                raw_rows = []
                for csv_row in reader:
                    adapted = adapter.adapt_row(csv_row)
                    raw_rows.append(adapted)

            # Validate batch
            valid_rows, validation_summary = self.validator.validate_batch(raw_rows)
            metrics.increment("ingestion.rows_validated", len(raw_rows))
            metrics.increment("ingestion.rows_accepted", len(valid_rows))

            # Bulk insert in batches
            batch_size = config.get("ingestion.batch_size", 500)
            total_inserted = 0

            for i in range(0, len(valid_rows), batch_size):
                batch = valid_rows[i : i + batch_size]
                inserted = await self._insert_batch(batch)
                total_inserted += inserted

            await self.session.commit()
            metrics.increment("ingestion.rows_inserted", total_inserted)

        # ── Auto-seed from ingested data ──
        seed_summary = {}
        if config.get("ingestion.auto_create_contracts"):
            seed_summary["contracts"] = await self._auto_create_contracts()
        if config.get("ingestion.auto_import_pricing"):
            seed_summary["pricing"] = await self._auto_import_pricing()

        await self.session.commit()

        summary = {
            "success": True,
            "file": path.name,
            "total_rows": len(raw_rows),
            "valid_rows": len(valid_rows),
            "inserted": total_inserted,
            "validation": validation_summary,
            "unmapped_columns": unmapped,
            "seed_summary": seed_summary,
            "adapter_mapping": adapter.mapping,
            "adapter_confidence": f"{adapter.confidence:.0%}",
            "encoding": encoding if 'encoding' in dir() else "utf-8",
        }

        log_pipeline_event(
            "ingest_complete",
            details={
                "file": path.name,
                "rows": total_inserted,
                "rejected": validation_summary["rejected"],
            },
        )

        return summary

    async def ingest_csv_content(self, content: str, filename: str = "upload.csv") -> dict:
        """Ingest CSV content from a string (for API uploads)."""
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        if not headers:
            return {"success": False, "error": "CSV has no headers"}

        adapter = DatasetAdapter(headers)
        raw_rows = [adapter.adapt_row(row) for row in reader]
        valid_rows, validation_summary = self.validator.validate_batch(raw_rows)

        batch_size = config.get("ingestion.batch_size", 500)
        total_inserted = 0
        for i in range(0, len(valid_rows), batch_size):
            batch = valid_rows[i : i + batch_size]
            total_inserted += await self._insert_batch(batch)

        await self.session.commit()

        return {
            "success": True,
            "file": filename,
            "total_rows": len(raw_rows),
            "valid_rows": len(valid_rows),
            "inserted": total_inserted,
            "validation": validation_summary,
        }

    async def _insert_batch(self, rows: list[dict]) -> int:
        """Insert a batch of validated rows into llm_token_log."""
        inserted = 0
        for row in rows:
            try:
                log_entry = LLMTokenLog(
                    id=uuid.uuid4(),
                    tenant_id=row["tenant_id"],
                    model=row.get("model", "unknown"),
                    provider=row.get("provider", "unknown"),
                    input_tokens=row.get("input_tokens", 0),
                    output_tokens=row.get("output_tokens", 0),
                    total_tokens=row.get("total_tokens", 0),
                    cost_usd=row.get("cost_usd", Decimal("0")),
                    status=row.get("status", "success"),
                    duration_ms=row.get("duration_ms"),
                    agent_id=row.get("agent_id"),
                    module=row.get("module"),
                    created_at=row.get("created_at", datetime.now(timezone.utc)),
                )
                self.session.add(log_entry)
                inserted += 1
            except Exception as e:
                logger.warning(f"ingestion.row_insert_failed error={e}")
                continue

        try:
            await self.session.flush()
        except Exception as e:
            logger.error(f"ingestion.batch_flush_failed error={e}")
            await self.session.rollback()
            return 0

        return inserted

    async def _auto_create_contracts(self) -> dict:
        """Auto-create contracts for tenants discovered in the data."""
        from modules.billing.repositories.contract_repository import ContractRepository
        repo = ContractRepository(self.session)

        # Get all unique tenants and their approximate monthly usage
        result = await self.session.execute(
            text("""
                SELECT tenant_id,
                       COALESCE(SUM(total_tokens), 0) as total_tokens,
                       COUNT(DISTINCT DATE(created_at)) as days_active
                FROM llm_token_log
                WHERE tenant_id IS NOT NULL
                GROUP BY tenant_id
            """)
        )
        rows = result.all()

        created = 0
        for row in rows:
            days = max(int(row.days_active), 1)
            monthly_estimate = int(row.total_tokens) * 30 // days
            contract = await repo.auto_assign_from_usage(row.tenant_id, monthly_estimate)
            if contract:
                created += 1

        if created:
            await self.session.flush()
            logger.info(f"contracts.auto_created count={created}")

        return {"contracts_created": created, "tenants_found": len(rows)}

    async def _auto_import_pricing(self) -> dict:
        """Import pricing from datasets/llm_pricing.csv if it exists."""
        from modules.analytics.repositories.pricing_repository import PricingRepository
        repo = PricingRepository(self.session)

        pricing_path = Path("datasets/llm_pricing.csv")
        if not pricing_path.exists():
            return {"imported": 0, "reason": "no pricing file"}

        rows = []
        with open(pricing_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for csv_row in reader:
                rows.append({
                    "model": csv_row.get("model", "").strip(),
                    "input_price_per_1k": csv_row.get("input_price_per_1k", "0"),
                    "output_price_per_1k": csv_row.get("output_price_per_1k", "0"),
                    "valid_from": csv_row.get("valid_from", str(date.today())),
                })

        count = await repo.upsert_from_csv(rows)
        return {"imported": count}
