# GPU worker on a home PC

A PC with an NVIDIA card can do the segmentation for the Mac mini, instead of Modal. The Mac sends a de-identified CT over Tailscale, the PC runs TotalSegmentator on its GPU and sends the organ map back. Nothing is kept on the PC except the model weights. The worker is `gpu_worker/server.py`, packaged as a Docker image.

What the card needs is graphics memory, not speed: roughly 8 GB for typical abdominal CTs, more for large thin-slice scans. A 12 GB card (RTX 4070 Ti) is enough; the Admin tab's test reports the peak so you can see how close a case comes.

## 1. Prepare the PC (Windows)

1. **NVIDIA driver:** install the current Game Ready or Studio driver, version 580 or newer. The worker's PyTorch build uses CUDA 13.
2. **WSL and Docker:** in an administrator PowerShell, `wsl --install`, restart, then install Docker Desktop with the WSL 2 backend (the default). Docker Desktop passes the NVIDIA GPU through to containers by itself.
3. **Tailscale:** install it and sign in to the same account as the Mac mini. Note the PC's Tailscale name (for example `home-pc`) and its 100.x.y.z address, shown in the Tailscale menu.
4. **Sleep:** set the PC not to sleep while it is serving, or switch the Admin tab back to Modal when the PC is off. The planner does not fall back to Modal by itself.

## 2. Build the worker

In PowerShell, in a folder of your choice:

```
git clone https://github.com/urologai/stone-planner.git
cd stone-planner
docker build -f gpu_worker/Dockerfile -t stone-planner-gpu .
```

The image is large (several GB: PyTorch with its CUDA libraries). Check that the container sees the card:

```
docker run --rm --gpus all stone-planner-gpu python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

It should print `True` and the card's name.

## 3. Run it

Make a token, a random string of at least 32 characters. On the Mac: `openssl rand -hex 32`. The same token goes on the Mac's Admin tab, so keep it somewhere only you can read.

```
docker run -d --name stone-gpu --restart unless-stopped --gpus all `
  -p 100.x.y.z:8100:8100 `
  -e WORKER_TOKEN=paste-the-token `
  -v stoneplanner-weights:/weights `
  stone-planner-gpu
```

- `-p 100.x.y.z:8100:8100` with the PC's Tailscale address publishes the worker only on Tailscale, not on the home network. If Docker refuses that address, publish `-p 8100:8100` and add a Windows Defender Firewall inbound rule that allows port 8100 only from `100.64.0.0/10` (Tailscale's range).
- `--restart unless-stopped` brings it back after a reboot, once Docker Desktop starts.
- `stoneplanner-weights` keeps the model weights (about 1.6 GB, downloaded on the first segmentation) across restarts.
- Tailscale encrypts the traffic between the two machines, so plain `http://` on the tailnet is fine. Never forward port 8100 on the router.

`docker logs stone-gpu` shows what it is doing. To update: `git pull`, build again, `docker rm -f stone-gpu`, and the run command again.

## 4. Connect the Mac mini

On the planner's Admin tab:

1. Segmentation: Home PC GPU.
2. GPU worker address: `http://home-pc:8100`, with the PC's Tailscale name (or `http://100.x.y.z:8100`).
3. Worker token: the token from step 3. Save.
4. Check connection: it should name the card and its memory, for example "NVIDIA GeForce RTX 4070 Ti, 12 GB".
5. Test segmentation: the first run includes the weights download. Run it twice and read the second: the time and the peak graphics memory. For a typical 1 mm abdominal CT, expect a peak well under the card's memory. If a large scan fails for lack of memory, that case can go to Modal.

## Notes

- The worker runs one CT at a time; a second request waits.
- TotalSegmentator's usage telemetry is switched off (`stoneplanner/segment.py`), here as on Modal and the Mac.
- A game running on the same card competes for its memory. Close it before segmenting.
- Linux with an NVIDIA card works the same way, with the NVIDIA Container Toolkit installed for `--gpus all`.
