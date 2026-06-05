# 🛡️ AI Governance & Monitoring Platform (LLM Dashboard)

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-000000?style=flat&logo=nextdotjs&logoColor=white)](https://nextjs.org)
[![TimescaleDB](https://img.shields.io/badge/TimescaleDB-F15A24?style=flat&logo=postgresql&logoColor=white)](https://www.timescale.com)
[![Redis](https://img.shields.io/badge/Redis-DC382D?style=flat&logo=redis&logoColor=white)](https://redis.io)
[![Celery](https://img.shields.io/badge/Celery-373737?style=flat&logo=celery&logoColor=white)](https://docs.celeryq.dev)

An enterprise-grade **AI Governance, Monitoring, and FinOps Platform** for LLM API usage. The system tracks token consumption, enforces strict budget caps, detects anomalies using statistical and ML models, and utilizes LLM-powered agents to explain anomalies and automatically optimize prompts and costs.

---

## 🏗️ Architecture: The M1–M10 Ecosystem

The platform is designed around ten modular subsystems (M1 to M10), separating the fast request hot-path, real-time analytics, background ML, and agentic AI helpers.

```mermaid
graph TD
    %% Clients and Gateway Ingestion
    Client[LLM Clients / Apps] -->|1. LLM API Call| M1[M1: Gateway Ingest]
    M1 -->|Validate & Intercept| M7[M7: Governance Engine]
    
    %% Cache and Storage
    M1 -->|0.1ms counters| Redis[(Redis: Quotas & Cache)]
    M1 -->|Async Log| EventBus[Event Bus: call.logged]
    
    %% Analytics & DB Ingestion
    EventBus -->|Process Log| M2[M2: Analytics Engine]
    M2 -->|Insert Time-series| DB[(PostgreSQL + TimescaleDB)]
    
    %% Anomaly Detection & Explanation Flow
    EventBus -->|Trigger Sweep| M3[M3: Anomaly Detection]
    M3 -->|Anomaly Detected| EventBus
    EventBus -->|Trigger Explainer| M4[M4: Explainer Agent]
    M4 -->|Use LLM| LLMProvider[Unified LLM Provider]
    M4 -->|explanation.generated| WS[M9: WS Server] -->|Live Update| UI[M6: Next.js Dashboard]
    
    %% Forecasting and Optimization
    Celery[Celery Scheduler] -->|Weekly Scan| M5[M5: Optimizer Agent]
    M5 -->|Prompt Compress / Model Suggestion| DB
    Celery -->|Daily Forecast| Forecasting[Forecasting Engine]
    
    %% Invoicing and Assistant
    Celery -->|Monthly Invoice| M10[M10: Billing Engine]
    M10 -->|Export Webhook| ERP[External ERP/CRM]
    
    UI -->|Ask Questions| M8[M8: Dashboard Assistant]
    M8 -->|Execute Query| DB
    M8 -->|Synthesize| LLMProvider
```

### 📦 Subsystem Breakdown

| Module | Name | Purpose | Implementation Detail |
| :--- | :--- | :--- | :--- |
| **M1** | **Gateway Ingestion** | Ingests usage records in three modes: `simulation`, `real`, `hybrid`. | Middleware rate limiter, Pydantic validations, async logging. |
| **M2** | **Analytics Engine** | Aggregates raw LLM metrics (token counts, latencies, cost) hourly and daily. | Celery tasks + SQL aggregation on TimescaleDB hyper-tables. |
| **M3** | **Anomaly Detection** | Monitors token/cost metrics and detects sudden spikes. | Ensemble approach: Z-Score, Isolation Forest, and CUSUM. |
| **M4** | **Explainer Agent** | Translates technical anomalies into clear explanations and business impact. | Agentic LLM parser fetching context and outputting formatted advice. |
| **M5** | **Optimizer Agent** | Audits prompt efficiency and suggests cheaper/faster model alternatives. | LLM-assisted prompt compressor & cost optimization assessor. |
| **M6** | **Dashboard UI** | Modern web portal to visualize cost, governance, and anomalies. | Next.js 15 app (`frontend_v2`) and lightweight raw HTML dashboard. |
| **M7** | **Governance Engine** | Enforces policy restrictions on active LLM API requests. | Redis-backed budget limit rules, model blocklists, and fallback paths. |
| **M8** | **Dashboard Assistant** | Conversational AI assistant helping admins query and audit data. | RAG-lite chatbot with SQL awareness and semantic vector helpers. |
| **M9** | **Tracing & Websockets** | Enforces correlation identifiers (Trace ID) and pushes live WS updates. | WebSockets push channel broadcasting alerts and logs to the UI. |
| **M10**| **Billing & Invoicing** | Automatically creates invoices based on tenant contracts (Base Fee + Overage). | ReportLab PDF generator, draft states, ERP webhooks, credits. |

---

## 🛠️ Technology Stack

* **Backend**: FastAPI (Python 3.13)
* **Task Queue**: Celery & Celery Beat (Periodic jobs scheduler)
* **Database**: PostgreSQL with **TimescaleDB** (time-series hyper-tables for fast log queries)
* **In-Memory Store**: Redis (Quota counters, rate limits, Celery broker, cache)
* **Frontend**: Next.js 15 (TypeScript, React, Tailwind CSS)
* **ML/Data Science**: Scikit-Learn (Isolation Forest), Statsmodels, Prophet, Numpy
* **LLM Clients**: Groq SDK, LiteLLM (multi-provider support)
* **Reporting**: ReportLab (Invoices in PDF)

---

## 🚀 Getting Started

### 📋 Prerequisites

Before running the application, make sure you have:
* Python 3.13+ installed
* Node.js v18+ & npm (for Next.js frontend)
* Docker & Docker Compose (optional but recommended)
* A [Groq Console API Key](https://console.groq.com/) (used for simulation and agents; free tier works out of the box)

---

### 1. Configuration (`.env`)

Clone the configuration template and customize the values:

```bash
cp .env.example .env
```

Ensure the following variables are set correctly:
* `DATABASE_URL`: Connection string for PostgreSQL (e.g. `postgresql+asyncpg://llmuser:llmpassword@localhost:5432/llmdashboard`)
* `REDIS_URL`: Redis database 0 (e.g. `redis://localhost:6379/0`)
* `REDIS_CACHE_URL`: Redis database 1 (e.g. `redis://localhost:6379/1`)
* `SECRET_KEY`: Used for JWT authentication tokens. Generate a secure one with: `python -c "import secrets; print(secrets.token_hex(32))"`
* `GROQ_API_KEY`: Required for simulation and LLM-assisted agents.
* `M4_DEFAULT_PROVIDER`: Provider to use for LLM agents (`groq` is recommended for speed and cost-free tier).

---

### 2. Local Setup (Without Docker)

If you prefer to run services individually:

#### A. Backend Setup
1. Install dependencies:
   ```bash
   pip install pipenv
   pipenv install
   ```
2. Run Database Migrations:
   ```bash
   pipenv run alembic upgrade head
   ```
3. Start the FastAPI backend:
   ```bash
   pipenv run uvicorn main:app --reload --port 8000
   ```
4. In separate terminal windows, start the Celery background worker and scheduler:
   ```bash
   # Run background worker
   pipenv run celery -A celery_app worker --loglevel=info
   
   # Run task scheduler (Beat)
   pipenv run celery -A celery_app beat --loglevel=info
   ```

#### B. Frontend Setup (`frontend_v2`)
1. Navigate to the frontend directory:
   ```bash
   cd frontend_v2
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the Next.js development server:
   ```bash
   npm run dev
   ```
   Open `http://localhost:3000` to access the dashboard.

---

### 3. Quickstart with Docker Compose (Recommended)

Docker Compose starts PostgreSQL (with TimescaleDB), Redis, FastAPI API, Celery Worker, Celery Beat, and Next.js UI automatically:

```bash
docker compose up --build
```

For production deployment with Nginx proxy and TLS enabled:
```bash
docker compose --profile production up --build
```

---

## 🚦 Traffic & Data Simulator

To see the platform's features in action immediately, you can generate simulated traffic representing real corporate usage.

The platform comes pre-configured with a simulator for **TIM Tunisia** (an industrial digital solutions provider) generating AI traffic for two product modules:
1. **QALITAS**: Quality, Health, Safety, and Environment (QHSE) management assistant.
2. **GMAO Pro**: Predictive maintenance scheduler and diagnostic tool.

The traffic simulates calls from three corporate tenants with different subscription tiers:
* **CAVEO Automotive** (Enterprise tier - $500 monthly budget limit)
* **SEAMTECH - LAKATECH** (Professional tier - $300 monthly budget limit)
* **General Beton** (Pay-As-You-Go tier - $150 budget limit)

### 🕹️ Running the Simulator

1. Make sure your backend API is running on `http://localhost:8000`.
2. Seed the database with tenants, contracts, governance limits, and base anomalies:
   ```bash
   pipenv run python simulate_traffic.py --seed-only
   ```
3. Generate realistic simulated LLM traffic (makes live calls to Groq/Llama-3.3 and routes logs through the M1 Gateway):
   ```bash
   # Generate 10 calls for all 3 tenants
   pipenv run python simulate_traffic.py --all-tenants --calls 10
   
   # Run simulation for a specific tenant
   pipenv run python simulate_traffic.py --tenant caveo_automotive --calls 15
   ```
4. Inject historical forecasts and artificial metric anomalies to visualize budget curves in the frontend:
   ```bash
   pipenv run python inject_fake_data.py
   ```

---

## 🧪 Testing

The codebase includes a comprehensive suite of unit and integration tests covering all critical gateway middleware, services, repositories, and Celery jobs.

To run the test suite:
```bash
pipenv run pytest
```

---

## 📁 Project Directory Structure

```
├── core/                  # Core modules (DB connection, Redis cache, rate limiting, logs)
│   ├── settings.py        # Pydantic configuration loader
│   ├── database.py        # SQLAlchemy engine and session management
│   ├── event_bus.py       # Event bus broker for inter-subsystem updates
│   └── rate_limit.py      # Middleware enforcing requests-per-second limits
├── modules/               # M1-M10 business domain modules
│   ├── auth/              # JWT authorization & user control
│   ├── gateway/           # M1 gateway routing & M7 governance rule executor
│   ├── analytics/         # M2 calculations & database aggregators
│   ├── detection/         # M3 anomaly detection & M4 explainer agent
│   ├── forecasting/       # Future estimation & M5 optimizer service
│   ├── billing/           # M10 invoice creation & ERP webhook dispatch
│   └── dashboard/         # Websockets (M9) and M8 conversational AI assistant
├── frontend_v2/           # Next.js 15 dashboard frontend (React + TS)
├── migrations/            # Alembic schema migration files
├── tests/                 # Full unit and integration test suite
├── Dockerfile             # Production container definitions for backend / celery
├── docker-compose.yml     # Local deployment topology configuration
├── simulate_traffic.py    # Demo seed & live traffic simulator runner
└── inject_fake_data.py    # Baseline and forecast visualizer data injector
```

---

## 🛡️ License

This project is licensed under the MIT License - see the LICENSE file for details.
