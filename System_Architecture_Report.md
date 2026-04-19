# System Architecture and Technical Engineering Report: Enterprise LLM Monitoring & Governance Platform

**Document Version:** 1.0.0
**Author:** houcine laghmani
**Status:** Approved for Engineering Review
**Target Audience:** Engineering Leadership, Technical Stakeholders, Investors

---

## 1. Introduction

### 1.1 Project Overview
The Enterprise LLM Monitoring & Governance Platform (herein referred to as the "Platform") is a comprehensive B2B SaaS solution engineered to provide centralized oversight, security, and financial control over Large Language Model (LLM) operations within enterprise environments. As organizations rapidly integrate AI capabilities into their workflows, they face unprecedented challenges regarding data privacy, prompt security, cost unpredictability, and fragmented API usage. This Platform acts as an intelligent proxy and unified control plane for all outbound LLM traffic.

### 1.2 Vision and Objectives
Our primary technical objective is to deliver a zero-trust, ultra-low-latency API gateway that securely sits between enterprise applications and third-party AI providers (e.g., OpenAI, Anthropic, Groq). The vision is to empower engineering and compliance teams to innovate with AI without compromising security or budget. 

Key objectives include:
- **Sub-50ms Latency Overhead:** The gateway must not bottleneck existing applications.
- **Strict Data Governance:** Real-time PII (Personally Identifiable Information) redaction and prompt injection detection.
- **Granular Financial Controls:** Token-level telemetry, cost attribution by department, and automated budget enforcement.

### 1.3 Problem Analysis
The "Shadow AI" phenomenon is accelerating. Developers often embed raw LLM API keys directly into microservices, leading to distributed, untraceable AI utilization. This architectural anti-pattern results in:
1. **Security Blind Spots:** Direct API calls bypass corporate firewalls and Data Loss Prevention (DLP) systems.
2. **Runaway Costs:** Uncapped model usage can exhaust budgets within hours (e.g., looping agents).
3. **Vendor Lock-in:** Hardcoded SDKs make it difficult to hot-swap AI providers when models degrade or pricing changes.

### 1.4 Why this SaaS Matters
This SaaS addresses a critical market gap: the lack of enterprise-grade observability tailored specifically for non-deterministic AI systems. By providing a unified AI gateway, we allow enterprises to route traffic dynamically, enforce global security policies, and achieve 100% visibility into their AI ROI (Return on Investment).

---

## 2. Functional Analysis

### 2.1 Detailed User Personas
To drive architectural decisions, we have defined three core personas:
- **The Platform Engineer (DevOps/SecOps):** Requires seamless integration (drop-in API URL replacement). Demands high availability, comprehensive logs, and standard metric exports (Prometheus/OpenTelemetry).
- **The FinOps Manager:** Focused exclusively on cost visibility. Needs dashboards showing token consumption translated into fiat currency, broken down by cost-center, project, or specific API key.
- **The Compliance Officer:** Requires immutable audit trails of all prompts and completions. Needs automated PII redaction and policy enforcement rules (e.g., "Halt any prompt containing credit card data").

### 2.2 Use Cases & Step-by-Step Scenarios

**Scenario 1: Dynamic Model Routing for High Availability**
1. An internal application requests a completion from `gpt-4`.
2. The Platform intercepts the request and detects that the OpenAI API is currently experiencing degraded performance (latency > 2000ms).
3. The Platform's routing engine automatically rewrites the payload and forwards the request to an equivalent fallback model (e.g., Anthropic's `claude-3-opus`).
4. The response is standardized and returned to the client application seamlessly.

**Scenario 2: Real-time Prompt Injection Defense & Governance**
1. A malicious user attempts to hijack a customer-facing AI chatbot using a known injection vector ("Ignore previous instructions...").
2. The request hits the Platform's Gateway module.
3. The Governance Service evaluates the prompt utilizing a specialized, high-speed classification model (embedded locally).
4. The requested is flagged with a high risk score.
5. The Platform outright blocks the request, returns an HTTP 403 Forbidden with a policy violation payload, and logs the attempt for SecOps.

### 2.3 Feature Breakdown
- **Intelligent Gateway:** A reverse proxy that handles multi-provider API translation.
- **Governance Engine:** Real-time semantic analysis for risk and compliance.
- **Billing & Telemetry:** Asynchronous capturing of token counts, latency, and cost calculation.
- **Forecasting:** Predictive modeling for budget burn rates.
- **Agent Tracing:** Correlation IDs to track multi-step autonomous AI agent reasoning.

---

## 3. System Architecture

### 3.1 Global Architecture: Modular Monolith Design
For the initial phases of this SaaS, we have deliberately bypassed a fully distributed microservices architecture in favor of a strictly bounded **Modular Monolith**. 

**Justification of Choice:**
Premature microservices introduce immense network latency, complex distributed tracing, and data consistency headaches. Our system requires atomic operations (e.g., deducting budget quotas before allowing an API call). A modular monolith allows us to compile the entire backend into a single deployable artifact while keeping domains strictly siloed internally (e.g., `modules/gateway`, `modules/billing`, `modules/governance`). When throughput demands exceed vertical scaling limits, these domains can be cleanly extracted into separate stateless services communicating over gRPC.

### 3.2 High-level System Design
The system is bifurcated into two planes:
1. **The Data Plane (Hot Path):** Responsible for evaluating, routing, and returning LLM traffic. This path is optimized for raw I/O throughput. No complex database commits happen sequentially here; telemetry is queued asynchronously.
2. **The Control Plane (Cold Path):** The backend API governing user configurations, policy rules, billing aggregation, and dashboard analytics.

### 3.3 Component Interaction Flow
*(Conceptual Diagram Description)*
- **Client Application** -> sends REST/WebSocket request -> **Load Balancer (Nginx/Envoy)**.
- **Load Balancer** -> routes to **FastAPI Gateway Module**.
- **Gateway Module** -> checks **Redis** for Rate Limits & Authentication.
- **Gateway Module** -> passes prompt to **Governance Service** (memory-bound, zero-network DB calls).
- **Gateway Module** -> forwards request to **External LLM Provider** (via async HTTP client).
- **Gateway Module** -> returns response to **Client**.
- *Concurrently*, **Gateway Module** -> pushes telemetry event (tokens, latency, IDs) to **Kafka/RabbitMQ / Background Task Queue**.
- **Billing/Analytics Worker** -> consumes queue -> persists to **PostgreSQL**.

---

## 4. Frontend Architecture

### 4.1 Framework Choice: React with TypeScript & Vite
We selected **React** powered by **Vite** as our frontend architecture. Vite provides instantaneous Hot Module Replacement (HMR) for developer velocity. **TypeScript** is strictly enforced to guarantee contract parity between the FastAPI backend schemas (OpenAPI/Pydantic) and the frontend props.

### 4.2 Component Structure
The UI follows an atomic design philosophy:
- **Atoms:** Highly reusable components (Buttons, Tooltips, Status Badges).
- **Molecules:** Form groups, Data metric cards (e.g., "Total Tokens Used").
- **Organisms:** Complex widgets, such as the Line Charts for latency tracking or the Billing Data Grid.
- **Templates/Pages:** The core dashboard container encompassing the Sidebar, Topbar, and injected content.

### 4.3 State Management
We avoid heavy global state (like Redux) in favor of **React Query (TanStack Query)** for server state and **Zustand** for transient UI state (e.g., sidebar collapse state, current dark/light theme). React Query automatically handles aggressive caching, background refetching, and pagination for our massive telemetry datasets, significantly reducing frontend boilerplate.

### 4.4 UI/UX Logic & Performance Optimization
Given the data-heavy nature of this SaaS (rendering thousands of log lines), performance is critical:
- **Virtualization:** The prompt log tables utilize DOM virtualization (`react-window`), ensuring that only the visible rows are attached to the DOM, keeping memory footprints negligible even with 10,000+ records.
- **Real-time WebSockets:** For live AI agent traces, we maintain a WebSocket connection instead of HTTP polling.
- **Premium Aesthetics:** We utilize a custom theme with strict CSS variables (Tailwind or Vanilla CSS) focusing on high-contrast data visualization, dark mode support for developer ergonomics, and micro-animations for state transitions to establish a premium, SaaS-native feel.

---

## 5. Backend Architecture

### 5.1 Server Structure & Framework
The backend is built around **FastAPI (Python 3.11+)**. Python is the undisputed lingua franca of AI, granting us native access to robust ML libraries (like `transformers` or embedding models) for our Governance module. FastAPI's native `asyncio` support is crucial because our application is intensely I/O bound (waiting for remote LLM APIs to respond). A single Node.js or synchronous Python thread would block; FastAPI allows thousands of concurrent requests via asynchronous event loops.

### 5.2 API Design
We expose a RESTful API for the Control Plane (configuration, billing fetching). However, the Gateway (Data Plane) exactly mimics the OpenAI API schema (e.g., `/v1/chat/completions`). This is a strategic decision: it allows clients to route their traffic through our system merely by changing the `base_url` in their existing OpenAI SDKs without rewriting any code.

### 5.3 Authentication & Authorization
Security is multi-layered:
- **Control Plane:** End-users authenticate to the Dashboard via **OAuth2 with JWT**.
- **Data Plane:** Microservices authenticate via **Platform API Keys**. We hash API keys utilizing SHA-256 before storing them in the database. When an incoming request hits the proxy, the provided key is hashed in memory and compared against a highly-available Redis cache to minimize database latency.

### 5.4 Business Logic Organization
Code is organized by bounded contexts (modules):
- `modules/detection`: Analyzes prompts for toxity and jailbreaks.
- `modules/gateway`: Handles HTTP connection pooling, retry logic, and fallback providers.
- `modules/billing`: Calculates floating-point arithmetic for token costs. 
- `modules/forecasting`: Employs linear regression and trailing-average algorithms to predict end-of-month spend.

---

## 6. Database Design

### 6.1 Database Type: PostgreSQL
**PostgreSQL** serves as our primary datastore. Its robust transaction support guarantees consistency for billing records, while its native JSONB column types provide the flexibility of a NoSQL database when storing arbitrary LLM metadata, unstructured prompt inputs, and variable response schemas.

### 6.2 Schema Design (Core Entities)
- **`organizations` / `users`**: Tenant isolation boundaries.
- **`api_keys`**: Hashed keys linked to specific projects and rate-limit tiers.
- **`gateway_logs`**: The heaviest table. Stores `request_id`, `timestamp`, `model_name`, `prompt_tokens`, `completion_tokens`, `latency_ms`, and `risk_score`.
- **`billing_ledgers`**: Immutable append-only log of cost deductions.

### 6.3 Data Flow & Indexing Strategy
The `gateway_logs` table will scale rapidly. To maintain read performance on dashboard queries:
- **BRIN (Block Range Indexes)** are applied to `timestamp` columns, as log data is inserted chronologically. This vastly reduces memory usage compared to B-Trees for time-series aggregation.
- **Composite B-Tree Indexes** on `(organization_id, timestamp)` to immediately isolate tenant data during querying.

### 6.4 Scalability Considerations
As data velocity increases, the `gateway_logs` table will be explicitly partitioned by month. For massive scale, we plan to migrate telemetry aggregation to **ClickHouse** or **TimescaleDB**, which specialize in high-throughput analytical queries, keeping PostgreSQL solely for relational state and configuration.

---

## 7. DevOps & Deployment

### 7.1 Containerization & Hosting Strategy
The entire stack is containerized using **Docker** and orchestratable via Kubernetes or managed services (e.g., AWS ECS, Google Cloud Run). 
For a scalable SaaS, stateless backend containers allow us to auto-scale dynamically based on CPU utilization and incoming HTTP request rates.

### 7.2 CI/CD Pipeline
We utilize **GitHub Actions** for our Continuous Integration / Continuous Deployment pipeline:
1. **Linting & Type Checking:** `ruff` for Python, `tsc` for TypeScript.
2. **Unit & Integration Testing:** Pytest suite runs against a spun-up PostgreSQL Ephemeral Docker container.
3. **Build:** Docker images are tagged using Git SHAs and pushed to a remote container registry.
4. **Deploy:** Infrastructure as Code (Terraform) triggers a rolling update in the production environment ensuring zero-downtime deployments.

### 7.3 Monitoring and Logging
System health observability is decoupled from business logic:
- **Prometheus** scrapes application metrics (e.g., P99 latency of upstream LLM providers, 5xx error rates).
- **Grafana** visualizes cluster health.
- We utilize structured JSON logging via Python internals to allow log aggregators (like Datadog or ELK stack) to parse error traces contextually.

---

## 8. Security Architecture

### 8.1 Data Protection Strategies
Tenant isolation is enforced dynamically at the database query level (Row Level Security paradigms or mandatory `org_id` WHERE clauses). Any payload containing raw user prompts is encrypted at rest using AES-256. 

### 8.2 The Prompt Injection Wall
Our most critical defense mechanism. The Governance module scrutinizes inbound payloads before execution. If heuristics or secondary models classify the input as an injection attempt, the prompt never reaches the downstream LLM. This prevents data exfiltration and protects the integrity of enterprise RAG (Retrieval-Augmented Generation) applications.

### 8.3 Mitigating Target Vulnerabilities
- **XSS & CSRF:** The React frontend inherently escapes data, preventing XSS. CSRF tokens or strict `SameSite` cookie policies are enforced for all dashboard state-changing operations.
- **SQL Injection:** We strictly use SQLAlchemy ORM or parameterized queries; raw SQL interpolations are blocked at the CI/CD level.
- **DDoS at the API Edge:** Redis-backed sliding-window rate limiters prevent individual API keys from saturating server workers.

---

## 9. Performance & Scalability

### 9.1 Load Handling & Asynchronous I/O
The system's bottleneck is network latency to OpenAI/Anthropic, not local CPU. FastAPI's asynchronous architecture handles this elegantly; a single container can keep thousands of TCP sockets open concurrently while spending zero CPU cycles during the "waiting" phase. 

### 9.2 Semantic Caching (Redis)
To radically improve performance and reduce API costs, we implement an intelligent Caching Layer. If a developer asks an identical or semantically similar question, the platform bypasses the LLM provider entirely.
- By utilizing lightweight embedding calculations (cosine similarity mapping) against a vector database or Redis, we can serve "cached" LLM responses in <10ms, essentially providing a 100% discount on redundant queries.

### 9.3 Horizontal Scaling
Because the backend instances share no state (all state lies in Redis and Postgres), scaling to handle a 100x traffic spike is achieved by simply spinning up more container replicas behind the load balancer.

---

## 10. AI Integration

Our system does not just route AI traffic; it uses AI to monitor AI.
- **Toxicity & PII Models:** To minimize latency, we use lightweight, specialized models (e.g., DistilBERT instances trained specifically on prompt injection datasets) running directly within our infrastructure. These models evaluate prompts in <20ms, which is completely acceptable network overhead.
- **Data Flow:** `Prompt -> Gateway -> Local BERT Model (Pass/Fail) -> External LLM Pipeline`.
- **Limitations:** Running large-scale evaluations on outputs (e.g., verifying if the AI hallucinated) is computationally expensive overhead. Thus, deep output evaluation runs asynchronously in the background rather than blocking the real-time API response.

---

## 11. Business Model

### 11.1 Monetization Strategy
The SaaS utilizes a hybrid model tailored for B2B procurement:
- **Seat-based Subscriptions:** A flat monthly fee for access to the Control Plane (Dashboards, team collaboration, governance policy configuration). 
- **Usage-based Expansion:** Billed volumetrically based on the gigabytes of log telemetry processed, or a fractional markup on the raw LLM API tokens routed through the platform.

### 11.2 Pricing Tiers
- **Developer (Free Tier):** Up to 100k requests monitored/mo. Community support.
- **Team (Pro Tier):** Unlimited requests, data retention (30 days), basic role-based access control, automated cost alerts.
- **Enterprise:** Custom data retention, VPC peering (deploying our gateway inside their private cloud), dedicated support SLAs, SSO/SAML integrations.

---

## 12. Future Improvements

### 12.1 Feature Roadmap
1. **Multi-Modal Capabilities:** Extending the gateway constraints and telemetry to parse and monitor image generation endpoints (DALL-E 3, Midjourney) and audio ingestion.
2. **Automated Red-Teaming:** A service that proactively attempts to hack a client's own AI endpoints to discover vulnerabilities before deployment.
3. **Advanced RAG Tracing:** Providing native visualizations showing exactly which database nodes/documents were retrieved prior to prompt generation.

### 12.2 Technical Upgrades
As read-queries grow significantly, we will migrate the logging architecture from PostgreSQL to an OLAP database (like ClickHouse) and introduce Apache Kafka as the backbone ingestion bus. This will segregate the transactional database entirely away from analytic workflows, guaranteeing sub-second dashboard rendering times regardless of scale.

---
*End of Technical Report*
