# Production image for the stone planner web app: one container serving the
# FastAPI API and the built React front end. Segmentation is not in here: it
# runs on a Modal GPU (modal_app/segment_gpu.py), so no PyTorch in this image.
#
#   docker build -t stone-planner .
#   docker run -p 8000:8000 -e APP_PASSWORD=... -e COOKIE_SECURE=false stone-planner
#
# Render builds this from the repo (render.yaml). Base images are pinned by
# digest so a rebuild cannot silently change them; refresh deliberately.

# --- Stage 1: build the front end -------------------------------------------
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime --------------------------------------------------
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    FRONTEND_DIST=/app/frontend_dist \
    WORK_DIR=/tmp/stoneplanner-cases

WORKDIR /app

# Hash-pinned lock (requirements-web.txt, generated from requirements-web.in):
# pip refuses any wheel that does not match, so nothing substituted gets in.
COPY requirements-web.txt ./
RUN pip install --require-hashes -r requirements-web.txt

COPY stoneplanner/ /app/stoneplanner/
COPY stoneplanner_api/ /app/stoneplanner_api/
COPY --from=frontend /fe/dist /app/frontend_dist

# Unprivileged runtime account. The only writable path the app uses is
# WORK_DIR under /tmp, wiped at start-up and on Render at every deploy.
RUN groupadd --system --gid 10001 planner \
 && useradd --system --uid 10001 --gid planner --no-create-home \
        --shell /usr/sbin/nologin planner
USER 10001:10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=4)"

# One worker on purpose: sessions' cases and job states live in this process.
# Render sets PORT; --proxy-headers trusts its X-Forwarded-* headers.
CMD ["sh", "-c", "exec uvicorn stoneplanner_api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
