# Running on the Mac mini

The current setup. The planner runs on one Mac mini that is allowed to hold PHI, and you use it by connecting to that Mac with Screen Sharing. Nothing is deployed to Render (on hold; see [deploy.md](deploy.md)).

| What | Where |
|---|---|
| The case workbook (MRNs and clinical data) | University OneDrive, synced to the Mac mini, read by the planner |
| DICOM exports | On the Mac mini while they are ingested |
| De-identified case folders, the MRN key file, the admin settings | The Mac mini |
| Segmentation | Chosen on the Admin tab: Modal (cloud GPU), the home PC GPU ([gpu-worker.md](gpu-worker.md)), or the Mac's own processor |

The planner listens only on 127.0.0.1, so no other computer can reach it directly. You reach the Mac itself over Tailscale (or the university VPN) with Screen Sharing, and open the planner in a browser on the Mac.

**Setting it up:** follow [get-it-running.md](get-it-running.md), Part A, step by step. This page is the background.

## Why it is set up this way

- **Bound to 127.0.0.1.** The planner answers only on the Mac itself; you reach the Mac with Screen Sharing over Tailscale. No web port is open to any network, so the login password is a second lock, not the only one.
- **FileVault.** The Mac holds PHI (DICOM exports during ingest, the MRN key, the OneDrive copy of the workbook). The cost is that after a power cut or restart someone has to unlock the Mac at the Mac; `sudo fdesetup authrestart` covers planned restarts.
- **Native install, not Docker.** It reads the OneDrive folder directly and can use the Apple GPU for local segmentation; Docker on a Mac can do neither reliably. The Docker route is below if you need it.
- **Launch agent.** `scripts/run_local.sh` starts the planner from `~/stone-key/stoneplanner.env` (which it refuses unless only you can read it), and launchd restarts it if it stops.
- **Settings outside the data folder.** `~/stone-key` holds the MRN key, the Admin tab's tokens and the password file; `~/stone-data` holds only de-identified case folders and can be shared.

## Updating

See [get-it-running.md](get-it-running.md#updating-later).

## Validation report

```
cd ~/stone-planner
.venv/bin/python scripts/validate.py ~/stone-data ~/stone-data/manual.csv ~/stone-report
```

## With Docker instead

The Docker image from [deploy.md](deploy.md) also runs on the Mac. Mount the OneDrive folder read-only and give its path inside the container on the Admin tab, for example `-v ~/Library/CloudStorage/OneDrive-University/Stone:/onedrive:ro` and `/onedrive/stone-planner-cases.xlsx`. Two limits: Docker cannot see the Apple GPU, so "This computer" is not available in the image, and mounting a OneDrive folder into Docker can be unreliable. The native install in [get-it-running.md](get-it-running.md) avoids both.
