# ============================================================
# LLM Dashboard — Production Dockerfile
# Multi-stage build for minimal image size
# ============================================================
# Build: docker build -t llm-dashboard .
# Run:   docker run -p 8000:8000 --env-file .env llm-dashboard
# ============================================================

# ── Stage 1: Dependencies ─────────────────────────────────────
FROM python:3.13-slim AS deps

WORKDIR /app

# System dependencies for psycopg2, numpy, scikit-learn, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install pipenv and export to requirements.txt for faster Docker builds
COPY Pipfile Pipfile.lock ./
RUN pip install --no-cache-dir pipenv \
    && pipenv requirements > requirements.txt \
    && pip install --no-cache-dir -r requirements.txt

# Download spaCy model (needed by PII detection)
RUN python -m spacy download en_core_web_sm 2>/dev/null || true


# ── Stage 2: Production image ─────────────────────────────────
FROM python:3.13-slim AS production

WORKDIR /app

# Runtime-only system deps (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app \
    && adduser --system --ingroup app app

# Copy installed Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application code
COPY core/ ./core/
COPY modules/ ./modules/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY frontend/ ./frontend/
COPY datasets/ ./datasets/
COPY main.py celery_app.py alembic.ini pytest.ini ./

# Create directories for runtime data
RUN mkdir -p /app/logs /app/tmp \
    && chown -R app:app /app

# Switch to non-root user
USER app

# Health check — uses the unauthenticated /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose API port
EXPOSE 8000

# Default command — production-grade uvicorn with multiple workers
# Override with docker-compose command for different configurations
CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--access-log", \
     "--log-level", "info"]
