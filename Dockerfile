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
FROM node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime --------------------------------------------------
FROM python:3.13.17-slim@sha256:fe14fd57a4c0361110eaa0c284112b189750bcb43fb04bb5d1ee7b7744027ccf AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    FRONTEND_DIST=/app/frontend_dist \
    WORK_DIR=/tmp/stoneplanner-cases

WORKDIR /app

# Refresh the image's Debian CA bundle through the package manager. This keeps
# the system trust store current independently of Python's certifi bundle.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Hash-pinned lock (requirements-web.txt, generated from requirements-web.in):
# pip refuses any wheel that does not match, so nothing substituted gets in.
COPY requirements-web.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2 \
 && python -m pip install --require-hashes -r requirements-web.txt

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
