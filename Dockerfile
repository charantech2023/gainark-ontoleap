# Use official lightweight Python 3.11 image
FROM python:3.11-slim

# Prevent Python from writing .pyc and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# Install system dependencies (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install lightweight CPU-only PyTorch first
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default GLiNER model during image build so runtime startup is instant
RUN python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_small-v2.1')"

# And the sentence encoder semantic_match.py proposes synonyms with (23M parameters,
# ~90MB). Without it the first alignment on each instance would fetch it from the Hub.
RUN python -c "from transformers import AutoModel, AutoTokenizer; n='sentence-transformers/all-MiniLM-L6-v2'; AutoTokenizer.from_pretrained(n); AutoModel.from_pretrained(n)"

# Copy application source code
COPY . .

# FIX #18: Run as non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

# Expose port (Cloud Run injects PORT environment variable at runtime)
EXPOSE 8080

# Health check.
# /api/health is the liveness endpoint and stays public even when ONTOLEAP_API_KEY is
# set; /api/info would return 401 under authentication and fail the healthcheck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Launch FastAPI app with Uvicorn
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
