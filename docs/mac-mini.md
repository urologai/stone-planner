# Running on the Mac mini

The current setup. The planner runs on one Mac mini that is allowed to hold PHI, and you use it by connecting to that Mac with Screen Sharing. Nothing is deployed to Render (on hold; see [deploy.md](deploy.md)).

| What | Where |
|---|---|
| The case workbook (MRNs and clinical data) | University OneDrive, synced to the Mac mini, read by the planner |
| DICOM exports | On the Mac mini while they are ingested |
| De-identified case folders, the MRN key file, the admin settings | The Mac mini |
| Segmentation | Chosen on the Admin tab: Modal (cloud GPU), the home PC GPU ([gpu-worker.md](gpu-worker.md)), or the Mac's own processor |

The planner listens only on 127.0.0.1, so no other computer can reach it directly. You reach the Mac itself over Tailscale (or the university VPN) with Screen Sharing, and open the planner in a browser on the Mac.

## 1. Prepare the Mac

1. **Disk encryption.** System Settings, Privacy & Security, FileVault: on. With FileVault on, the Mac waits at its unlock screen after a power cut or an update restart until someone types the password at the Mac. Plan restarts with `sudo fdesetup authrestart`, which unlocks once for the next boot.
2. **Stay awake.** System Settings, Energy: prevent automatic sleeping when the display is off, and start up automatically after a power failure. Or in Terminal: `sudo pmset -a sleep 0 autorestart 1`.
3. **Firewall.** System Settings, Network, Firewall: on.
4. **Remote access.** Install Tailscale on the Mac and on the computer you connect from, and sign both in to the same account. System Settings, General, Sharing: turn on Screen Sharing and allow only your own account. Connect with Screen Sharing to the Mac's Tailscale name. Never forward a router port to the Mac.
5. **OneDrive.** Install the OneDrive app and sign in with the university account. In Finder, right-click the project folder that holds the workbook and choose Always Keep on This Device; a placeholder file cannot be read. The workbook's path looks like `/Users/you/Library/CloudStorage/OneDrive-University/Stone/stone-planner-cases.xlsx` (right-click the file with the Option key held, Copy as Pathname).
6. **Backups.** If Time Machine runs, back up to an encrypted disk. The MRN key and the OneDrive copy are PHI.

## 2. Install the planner

Install the tools once, with [Homebrew](https://brew.sh):

```
brew install python@3.13 node@22 git
```

Then the planner:

```
cd ~
git clone https://github.com/urologai/stone-planner.git
cd stone-planner
python3.13 -m venv .venv
.venv/bin/pip install -e ".[web]"
cd frontend && npm ci && npm run build && cd ..
```

To let the Mac segment on its own processor too (the Admin tab's "This computer" choice, slow but needs nothing else), install the segmentation extra as well: `.venv/bin/pip install -e ".[web,segment]"`. That adds PyTorch and TotalSegmentator, several GB.

DICOM conversion uses the `dcm2niix` package installed with the planner. If ingest reports it missing, `brew install dcm2niix`.

## 3. Folders and settings

```
mkdir -p ~/stone-data ~/stone-key ~/stone-report
chmod 700 ~/stone-key
```

- `~/stone-data`: de-identified case folders and `manual.csv`.
- `~/stone-key`: the MRN key file, the admin settings (GPU tokens), and the file below. Keep it out of OneDrive and any other synced or shared folder.

Create `~/stone-key/stoneplanner.env` with these lines, replacing the password, and make it readable only by you:

```
APP_PASSWORD=choose-a-long-password
SECRET_KEY=paste-the-output-of: openssl rand -hex 32
COOKIE_SECURE=false
DATA_DIR=/Users/you/stone-data
KEY_FILE=/Users/you/stone-key/mrn_key.csv
```

```
chmod 600 ~/stone-key/stoneplanner.env
```

`SECRET_KEY` keeps you logged in across restarts. `COOKIE_SECURE=false` is right here because the browser talks to the planner over plain HTTP on the same machine.

Try it by hand first: `~/stone-planner/scripts/run_local.sh`, then open http://localhost:8000 and log in. Control-C stops it.

## 4. Start it automatically

Create `~/Library/LaunchAgents/org.stoneplanner.app.plist`, with your user name in the three paths:

```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>org.stoneplanner.app</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>/Users/you/stone-planner/scripts/run_local.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/you/Library/Logs/stoneplanner.log</string>
  <key>StandardErrorPath</key><string>/Users/you/Library/Logs/stoneplanner.log</string>
</dict>
</plist>
```

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.stoneplanner.app.plist
```

It now starts at login and restarts if it stops. To stop it: `launchctl bootout gui/$(id -u)/org.stoneplanner.app`. The log is `~/Library/Logs/stoneplanner.log`.

If the Admin tab's Check workbook says permission denied while the same path works by hand in Terminal, macOS is keeping background programs out of the OneDrive folder. Run the planner from Terminal instead (step 3), or allow it under System Settings, Privacy & Security, Files and Folders. This part has not been tested on a Mac yet.

## 5. Set it up on the Admin tab

1. **Segmentation:** pick Cloud GPU (Modal), Home PC GPU, This computer, or Off. Enter the token for the one you pick, press Save, then Check connection. Test segmentation sends one de-identified CT (the newest ingested case, or a synthetic phantom) and reports the time, and for the home PC the peak graphics memory.
2. **Case workbook:** paste the OneDrive path from step 1.5, Save, then Check workbook. The Ingest tab then has Load linked workbook.

Modal needs its function deployed once and a token: [deploy.md](deploy.md), section 1. The home PC needs the worker: [gpu-worker.md](gpu-worker.md).

## Updating

```
cd ~/stone-planner
git pull
.venv/bin/pip install -e ".[web]"
cd frontend && npm ci && npm run build && cd ..
launchctl kickstart -k gui/$(id -u)/org.stoneplanner.app
```

## Validation report

```
cd ~/stone-planner
.venv/bin/python scripts/validate.py ~/stone-data ~/stone-data/manual.csv ~/stone-report
```

## With Docker instead

The Docker image from [deploy.md](deploy.md) also runs on the Mac. Mount the OneDrive folder read-only and give its path inside the container on the Admin tab, for example `-v ~/Library/CloudStorage/OneDrive-University/Stone:/onedrive:ro` and `/onedrive/stone-planner-cases.xlsx`. Two limits: Docker cannot see the Apple GPU, so "This computer" is not available in the image, and mounting a OneDrive folder into Docker can be unreliable. The native install above avoids both.
