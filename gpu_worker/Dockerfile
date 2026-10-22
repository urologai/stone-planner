# GPU segmentation worker for a machine you run (a home PC with an NVIDIA card).
# Build from the repository root:
#   docker build -f gpu_worker/Dockerfile -t stone-planner-gpu .
# Run with the GPU, a token, and a volume for the model weights:
#   docker run -d --name stone-gpu --restart unless-stopped --gpus all \
#     -p <tailscale-ip>:8100:8100 -e WORKER_TOKEN=<token> \
#     -v stoneplanner-weights:/weights stone-planner-gpu
# See docs/gpu-worker.md. This is the official Linux AMD64 child digest; the
# worker lock targets x86_64 manylinux wheels and CUDA packages.
FROM --platform=linux/amd64 python:3.13.17-slim@sha256:d879ed88565a7a3c614b980b369d4a5025a9d057b71f93d09fc6d0b2ca823a18

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgl1 libglib2.0-0 \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY gpu_worker/requirements.txt ./
RUN python -m pip install --no-cache-dir --require-hashes --no-deps \
    "https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" \
 && python -m pip install --no-cache-dir --require-hashes -r requirements.txt

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
