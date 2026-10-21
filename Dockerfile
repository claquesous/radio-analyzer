# essentia-tensorflow only ships x86_64 Linux wheels, so build and run this image
# as linux/amd64 (docker-compose.yml sets the platform; Rosetta on Apple silicon).
FROM python:3.12-slim

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends libchromaprint-tools curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

# Model weights are baked into the image so the service runs offline after build.
ENV MODELS_DIR=/models HF_HOME=/models/hf
RUN mkdir -p $MODELS_DIR && cd $MODELS_DIR && \
    curl -sfLO https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb && \
    curl -sfLO https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb && \
    curl -sfLO https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.json && \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# Models are baked in above: never reach out to the Hugging Face hub at runtime.
ENV HF_HUB_OFFLINE=1 TF_CPP_MIN_LOG_LEVEL=2

# On Lambda, the Web Adapter extension turns each invocation into an HTTP request
# to this server, once /health answers. Anywhere else it never starts, and the
# image is a plain HTTP service.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8000 AWS_LWA_READINESS_CHECK_PATH=/health

COPY . .

RUN useradd -m -u 1000 analyzer && mkdir -p /cache && chown -R analyzer /cache $MODELS_DIR
USER analyzer

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD curl -sf http://localhost:8000/health || exit 1
ENTRYPOINT ["python", "-m", "analyzer"]
