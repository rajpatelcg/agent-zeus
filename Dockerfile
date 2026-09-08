# ---- Base image ----
FROM python:3.11-slim AS base

# Install uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Prevents Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ---- Azure Monitor / OpenTelemetry ENV ----
ENV OTEL_METRIC_EXPORT_INTERVAL=5000 \
    OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta \
    APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=ac32954b-8216-46e7-b439-330ae3a85d8c;IngestionEndpoint=https://westeurope-5.in.applicationinsights.azure.com/;LiveEndpoint=https://westeurope.livediagnostics.monitor.azure.com/;ApplicationId=41e47ccd-6d72-4d03-a561-a550e366e9bd"

# Set work directory
WORKDIR /app

# ---- System dependencies ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libasound2 \
    libssl-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ---- Install dependencies ----
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Copy application code ----
COPY . .

RUN  uv pip install -r requirements.txt
#
RUN . .venv/bin/activate && \
    uv pip install -r requirements.txt

# ---- Install Azure Monitor OpenTelemetry ----
# RUN . .venv/bin/activate && \
#     pip install --no-cache-dir \
#       opentelemetry-sdk \
#       azure-monitor-opentelemetry && \
#     pip install --upgrade --force-reinstall setuptools==65.5.1 && \
       


# ---- Create non-root user ----
RUN useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

# Add virtualenv to PATH
ENV PATH="/app/.venv/bin:$PATH"

# ---- Expose port ----
EXPOSE 8000

# ---- Start server ----
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000"]
