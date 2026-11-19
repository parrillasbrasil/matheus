# Coletor, worker e CLI do radar. Contexto de build: a RAIZ do repositório.
#   docker build -f collector/Dockerfile --target runtime -t radar-collector .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY collector/pyproject.toml ./collector/pyproject.toml
COPY collector/radar ./collector/radar
RUN pip install ./collector
COPY config.yaml ./config.yaml
COPY db ./db

# Testes: inclui pytest e as fixtures (rotuladas como TESTE). Só roda com RADAR_ENV=test.
FROM base AS test
RUN pip install "./collector[dev]"
COPY collector/tests ./collector/tests
COPY scripts ./scripts
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
ENV RADAR_ENV=test RADAR_REPO_ROOT=/app
WORKDIR /app/collector
CMD ["python", "-m", "pytest"]

# Produção (último estágio = padrão). Usuário sem privilégios.
FROM base AS runtime
RUN useradd --system --uid 10001 --no-create-home radar
USER radar
ENV RADAR_CONFIG=/app/config.yaml RADAR_MIGRATIONS_DIR=/app/db/migrations
CMD ["radar", "worker"]
