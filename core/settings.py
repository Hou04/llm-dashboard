"""
Application settings — loaded once at startup from .env

All configuration lives here. No other file reads environment
variables directly. If a required variable is missing, the
application refuses to start with a clear error message.

Usage anywhere in the codebase:
    from core.settings import settings
    db_url = settings.database_url
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------
    database_url: str = Field(
        description="Full PostgreSQL connection string with asyncpg driver"
    )

    # --------------------------------------------------------
    # REDIS
    # --------------------------------------------------------
    redis_url: str = Field(
        description="Redis URL for quota counters (database 0)"
    )
    redis_cache_url: str = Field(
        description="Redis URL for rule cache (database 1)"
    )

    # --------------------------------------------------------
    # CONNECTION POOL TUNING
    # --------------------------------------------------------
    db_pool_size: int = Field(
        default=10,
        description="SQLAlchemy connection pool size (concurrent connections)"
    )
    db_max_overflow: int = Field(
        default=20,
        description="Max temporary connections above pool_size under load"
    )
    db_pool_recycle: int = Field(
        default=1800,
        description="Recycle connections after N seconds (prevents stale connections)"
    )
    redis_max_connections: int = Field(
        default=50,
        description="Max Redis connections per pool"
    )

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------
    app_env: str = Field(
        default="development",
        description="Environment: development, staging, production"
    )
    gateway_mode: str = Field(
        default="simulation",
        description="Gateway operating mode: simulation, real, hybrid"
    )
    log_level: str = Field(
        default="DEBUG",
        description="Logging level: DEBUG, INFO, WARNING, ERROR"
    )
    secret_key: str = Field(
        description="Secret key for signing tokens"
    )
    admin_password: str = Field(
        default="Admin@1234",
        description="Default password for the initial super_admin account. Should be overridden in .env for production."
    )

    # --------------------------------------------------------
    # CORS — comma-separated allowed origins
    # --------------------------------------------------------
    allowed_origins: str = Field(
        default="http://localhost:8000,http://localhost:3000,http://127.0.0.1:8000",
        description="Comma-separated list of allowed CORS origins"
    )

    # --------------------------------------------------------
    # COMPANY BRANDING (PDF invoices, emails, webhooks)
    # --------------------------------------------------------
    company_name: str = Field(
        default="LLM Dashboard Platform",
        description="Company name for invoices and reports"
    )
    company_tagline: str = Field(
        default="AI Monitoring & Governance",
        description="Company tagline for PDF headers"
    )
    company_address: str = Field(
        default="Paris, France",
        description="Company address for PDF footers"
    )
    company_email: str = Field(
        default="billing@llm-dashboard.io",
        description="Billing contact email"
    )
    company_web: str = Field(
        default="https://llm-dashboard.io",
        description="Company website URL"
    )

    # --------------------------------------------------------
    # API VERSION
    # --------------------------------------------------------
    api_version: str = Field(
        default="2026-04-01",
        description="API version string for webhook payloads"
    )

    # --------------------------------------------------------
    # DEFAULT GOVERNANCE LIMITS
    # --------------------------------------------------------
    default_daily_token_limit: int = Field(
        default=1_000_000,
        description="Default daily token limit per tenant"
    )
    default_monthly_token_limit: int = Field(
        default=20_000_000,
        description="Default monthly token limit per tenant"
    )
    default_monthly_budget_usd: float = Field(
        default=100.0,
        description="Default monthly budget in USD per tenant"
    )

    # --------------------------------------------------------
    # LLM API KEYS
    # All optional — missing keys trigger the fallback chain.
    # --------------------------------------------------------
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for GPT models"
    )
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key for Claude models"
    )
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key"
    )
    mistral_api_key: str = Field(
        default="",
        description="Mistral AI API key"
    )
    groq_api_key: str = Field(
        default="",
        description="Groq API key — free Llama, Mixtral models (https://console.groq.com)"
    )

    # --------------------------------------------------------
    # M4 / M5 LLM PROVIDER CONFIGURATION
    # Controls which LLM provider and model M4 (Explainer)
    # and M5 (Optimizer) use for their LLM calls.
    # Default: Groq with Llama-3.3-70b (free, no credit card).
    # Supported providers: groq, anthropic, openai, gemini, mistral, ollama
    # --------------------------------------------------------
    m4_default_provider: str = Field(
        default="groq",
        description="LLM provider for M4/M5: groq, anthropic, openai, gemini, mistral, ollama"
    )
    m4_default_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Default model for M4/M5 LLM calls"
    )
    m4_ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama base URL for local/free models"
    )
    m4_ollama_model: str = Field(
        default="llama3.2",
        description="Ollama model name (free, runs locally)"
    )

    # --------------------------------------------------------
    # PYDANTIC CONFIGURATION
    # --------------------------------------------------------
    model_config = {
        "env_file":          ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive":    False,
        "extra":             "ignore",   # ignore unknown env vars silently
    }


@lru_cache()
def get_settings() -> Settings:
    """
    Return the Settings singleton.

    lru_cache ensures .env is read exactly once per process.
    Call get_settings.cache_clear() in tests that need fresh settings.
    """
    return Settings()


settings = get_settings()