"""
Simulator Configuration
========================
All constants, tenant definitions, model pricing, statistical parameters.
"""

import os

# ============================================================
# API / AUTH
# ============================================================
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/v1")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "Admin@1234")

# ============================================================
# SIMULATION PARAMETERS
# ============================================================
RANDOM_SEED = 42  # Deterministic for reproducible demos
HISTORY_DAYS = 60  # Days of historical data to generate
CALLS_PER_DAY_BASE = 45  # Average calls per tenant per day (weekday)
WEEKEND_FACTOR = 0.30  # Weekend traffic = 30% of weekday
LIVE_INTERVAL_MIN = 2.0  # Min seconds between live calls
LIVE_INTERVAL_MAX = 5.0  # Max seconds between live calls

# ============================================================
# MODEL DEFINITIONS & PRICING (per 1K tokens, USD)
# ============================================================
MODELS = {
    "llama-3.3-70b-versatile": {
        "provider": "groq",
        "input_price_per_1k": 0.00059,
        "output_price_per_1k": 0.00079,
        "avg_latency_ms": 800,
        "latency_std": 300,
    },
    "llama-3.1-8b-instant": {
        "provider": "groq",
        "input_price_per_1k": 0.00005,
        "output_price_per_1k": 0.00008,
        "avg_latency_ms": 350,
        "latency_std": 150,
    },
    "gemma2-9b-it": {
        "provider": "groq",
        "input_price_per_1k": 0.00020,
        "output_price_per_1k": 0.00020,
        "avg_latency_ms": 500,
        "latency_std": 200,
    },
    "mixtral-8x7b-32768": {
        "provider": "groq",
        "input_price_per_1k": 0.00024,
        "output_price_per_1k": 0.00024,
        "avg_latency_ms": 600,
        "latency_std": 250,
    },
}

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_PROVIDER = "groq"

# ============================================================
# PROVIDER HEALTH DEFINITIONS
# ============================================================
PROVIDERS = {
    "groq": {"status": "online", "latency_ms": 450, "uptime_pct": 99.8},
    "openai": {"status": "online", "latency_ms": 890, "uptime_pct": 99.5},
    "anthropic": {"status": "online", "latency_ms": 720, "uptime_pct": 99.7},
    "mistral": {"status": "degraded", "latency_ms": 1200, "uptime_pct": 97.2},
    "google": {"status": "online", "latency_ms": 650, "uptime_pct": 99.9},
}

# ============================================================
# TENANT DEFINITIONS
# ============================================================
TENANTS = {
    "caveo_automotive": {
        "name": "CAVEO Automotive",
        "tier": "enterprise",
        "budget": 500.0,
        "contact": "anis.ezzine@caveo.tn",
        "contract": {
            "type": "forfait",
            "base_fee": 200.0,
            "tokens": 5_000_000,
            "overage": 0.03,
        },
        "agents": [
            "nc_classifier",
            "audit_report_gen",
            "risk_analyzer",
            "panne_diagnostic",
            "bon_travail_gen",
            "maintenance_predict",
        ],
        "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
        "weight": 0.45,
        "daily_calls_mean": 55,
        "daily_calls_std": 12,
        "growth_pct_per_week": 4.0,  # 4% weekly growth → visible in forecasting
    },
    "seamtech_lakatech": {
        "name": "SEAMTECH - LAKATECH",
        "tier": "professional",
        "budget": 300.0,
        "contact": "supply.chain@seamtech.tn",
        "contract": {
            "type": "forfait",
            "base_fee": 150.0,
            "tokens": 3_000_000,
            "overage": 0.025,
        },
        "agents": [
            "nc_classifier",
            "audit_report_gen",
            "supplier_evaluator",
            "stock_optimizer",
        ],
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "weight": 0.35,
        "daily_calls_mean": 35,
        "daily_calls_std": 8,
        "growth_pct_per_week": 2.5,
    },
    "general_beton": {
        "name": "Général Béton",
        "tier": "pay_as_you_go",
        "budget": 150.0,
        "contact": "qualite@generalbeton.tn",
        "contract": {
            "type": "pay_as_you_go",
            "base_fee": 0.0,
            "tokens": 0,
            "overage": 0.0,
        },
        "agents": [
            "nc_classifier",
            "revue_direction",
            "audit_report_gen",
        ],
        "models": ["llama-3.1-8b-instant", "gemma2-9b-it"],
        "weight": 0.20,
        "daily_calls_mean": 20,
        "daily_calls_std": 6,
        "growth_pct_per_week": 1.0,
    },
}

# ============================================================
# GOVERNANCE RULES
# ============================================================
GOVERNANCE_RULES = [
    {
        "rule_type": "budget_cap",
        "tenant_id": "general_beton",
        "monthly_budget_usd": 0.5,
        "priority": 200,
        "description": "Général Béton — budget plafond 0.50$/mois (pay-as-you-go)",
    },
    {
        "rule_type": "tenant_limit",
        "tenant_id": "general_beton",
        "daily_token_limit": 50_000,
        "priority": 150,
        "description": "Général Béton — limite 50K tokens/jour",
    },
    {
        "rule_type": "budget_cap",
        "tenant_id": "caveo_automotive",
        "monthly_budget_usd": 1.0,
        "priority": 200,
        "description": "CAVEO Automotive — budget plafond 1.00$/mois (forfait enterprise)",
    },
    {
        "rule_type": "tenant_limit",
        "tenant_id": "seamtech_lakatech",
        "daily_token_limit": 80_000,
        "priority": 150,
        "description": "SEAMTECH — limite 80K tokens/jour",
    },
    {
        "rule_type": "model_block",
        "tenant_id": None,
        "model_name": "gpt-4",
        "priority": 300,
        "description": "Bloquer GPT-4 globalement — trop cher, utiliser Groq/Llama",
    },
]

# ============================================================
# ANOMALY INJECTION SCHEDULE
# Defines which days (relative to today) get anomalous data
# ============================================================
ANOMALY_DAYS = [
    {
        "days_ago": 0,
        "tenant_id": "caveo_automotive",
        "type": "token_spike",
        "severity": "critical",
        "multiplier": 3.5,  # 350% of normal usage
        "detectors": "stl,isolation_forest,cusum",
        "description": "CRITIQUE : utilisation quotidienne (450 000 tokens) 200% au-dessus de la baseline (150 000). Détecteurs : [stl, isolation_forest, cusum].",
    },
    {
        "days_ago": 0,
        "tenant_id": "seamtech_lakatech",
        "type": "cost_spike",
        "severity": "warning",
        "multiplier": 2.5,
        "detectors": "isolation_forest",
        "description": "ATTENTION : coût (12.50$) significativement supérieur à la moyenne journalière (4.20$). Utilisation anormale détectée.",
    },
    {
        "days_ago": 15,
        "tenant_id": "caveo_automotive",
        "type": "error_rate_spike",
        "severity": "high",
        "multiplier": 1.5,
        "detectors": "stl,cusum",
        "description": "ÉLEVÉ : taux d'erreur (15%) nettement supérieur au taux normal (3%). Dégradation potentielle du fournisseur.",
    },
    {
        "days_ago": 22,
        "tenant_id": "general_beton",
        "type": "token_spike",
        "severity": "warning",
        "multiplier": 2.8,
        "detectors": "isolation_forest,cusum",
        "description": "ATTENTION : pic de tokens détecté pour Général Béton. Vérifier l'activité des agents.",
    },
]

# ============================================================
# AGENT TOKEN PROFILES
# Different agents have different token consumption patterns.
# ============================================================
AGENT_PROFILES = {
    "nc_classifier": {
        "input_tokens_mean": 450,
        "input_tokens_std": 120,
        "output_tokens_mean": 350,
        "output_tokens_std": 100,
        "module": "QALITAS",
    },
    "audit_report_gen": {
        "input_tokens_mean": 600,
        "input_tokens_std": 150,
        "output_tokens_mean": 800,
        "output_tokens_std": 250,
        "module": "QALITAS",
    },
    "risk_analyzer": {
        "input_tokens_mean": 550,
        "input_tokens_std": 130,
        "output_tokens_mean": 650,
        "output_tokens_std": 200,
        "module": "QALITAS",
    },
    "revue_direction": {
        "input_tokens_mean": 700,
        "input_tokens_std": 180,
        "output_tokens_mean": 1000,
        "output_tokens_std": 300,
        "module": "QALITAS",
    },
    "supplier_evaluator": {
        "input_tokens_mean": 500,
        "input_tokens_std": 140,
        "output_tokens_mean": 550,
        "output_tokens_std": 160,
        "module": "QALITAS",
    },
    "panne_diagnostic": {
        "input_tokens_mean": 480,
        "input_tokens_std": 110,
        "output_tokens_mean": 600,
        "output_tokens_std": 180,
        "module": "GMAO_PRO",
    },
    "bon_travail_gen": {
        "input_tokens_mean": 350,
        "input_tokens_std": 90,
        "output_tokens_mean": 500,
        "output_tokens_std": 150,
        "module": "GMAO_PRO",
    },
    "maintenance_predict": {
        "input_tokens_mean": 420,
        "input_tokens_std": 100,
        "output_tokens_mean": 550,
        "output_tokens_std": 170,
        "module": "GMAO_PRO",
    },
    "stock_optimizer": {
        "input_tokens_mean": 380,
        "input_tokens_std": 95,
        "output_tokens_mean": 450,
        "output_tokens_std": 130,
        "module": "GMAO_PRO",
    },
}
