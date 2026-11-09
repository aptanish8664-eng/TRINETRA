FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system trinetra \
    && useradd --system --gid trinetra --home-dir /app --no-create-home trinetra \
    && install -d -o trinetra -g trinetra /app/backend

WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY --chown=trinetra:trinetra backend/app /app/backend/app
COPY --chown=trinetra:trinetra backend/alembic /app/backend/alembic
COPY --chown=trinetra:trinetra backend/alembic.ini /app/backend/alembic.ini
WORKDIR /app/backend

USER trinetra
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/api/health', timeout=3)"]
CMD ["python", "-c", "import os, uvicorn; uvicorn.run('app.main:app', host='0.0.0.0', port=int(os.getenv('PORT', '8000')), workers=1)"]
