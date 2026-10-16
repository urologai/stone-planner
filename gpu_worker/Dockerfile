# GPU segmentation worker for a machine you run (a home PC with an NVIDIA card).
# Build from the repository root:
#   docker build -f gpu_worker/Dockerfile -t stone-planner-gpu .
# Run with the GPU, a token, and a volume for the model weights:
#   docker run -d --name stone-gpu --restart unless-stopped --gpus all \
#     -p <tailscale-ip>:8100:8100 -e WORKER_TOKEN=<token> \
#     -v stoneplanner-weights:/weights stone-planner-gpu
# See docs/gpu-worker.md. Same base image digest as the web image.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY gpu_worker/requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY stoneplanner/ /app/stoneplanner/
COPY gpu_worker/__init__.py gpu_worker/server.py /app/gpu_worker/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    TOTALSEG_HOME_DIR=/weights/totalseg

RUN useradd --uid 10001 --create-home worker && mkdir -p /weights && chown worker /weights
USER worker

EXPOSE 8100
CMD ["uvicorn", "gpu_worker.server:app_from_env", "--factory", "--host", "0.0.0.0", \
     "--port", "8100"]
