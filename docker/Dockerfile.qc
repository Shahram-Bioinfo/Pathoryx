# QC inference service image.
# Requires NVIDIA runtime for GPU support.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libopenslide0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY pathoryx_enterprise/ pathoryx_enterprise/

# Install torch separately so it can be overridden for CUDA versions
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir \
    psycopg2-binary sqlalchemy alembic \
    pydantic pydantic-settings structlog \
    prometheus-client opentelemetry-sdk \
    opentelemetry-exporter-otlp-proto-grpc \
    python-dotenv pyyaml opencv-python-headless pillow numpy

RUN pip install --no-cache-dir torch torchvision --index-url ${TORCH_INDEX_URL}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd -r pathoryx && useradd -r -g pathoryx pathoryx
USER pathoryx

EXPOSE 8082 9092

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8082/ready')"

CMD ["pathoryx-qc"]
