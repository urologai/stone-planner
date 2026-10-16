# Get it running: Mac mini and the 4070 Ti PC

Every step to go from two bare machines to a first real case: the Mac mini runs the planner, the home PC with the RTX 4070 Ti does the segmentation. Each step ends with a check that tells you it worked. Do the parts in order; Part B can wait if you want the Mac working first.

When you are done:

- The Mac mini runs the planner around the clock, reachable only from itself. You use it over Screen Sharing.
- The planner reads the case workbook from the OneDrive folder on the Mac.
- The PC runs the GPU worker. The Mac sends it de-identified CTs over Tailscale and gets organ maps back.

You need: an admin account on both machines, your university Microsoft account (for OneDrive), and a free Tailscale account (sign up at tailscale.com with any account you like; use the same one on every machine).

Background and reference: [mac-mini.md](mac-mini.md), [gpu-worker.md](gpu-worker.md).

---

## Part A: the Mac mini

Do all of Part A sitting at the Mac, or over Screen Sharing once step A5 is done.

### A1. Update macOS

System Settings, General, Software Update: install what is offered and restart.

### A2. Turn on FileVault

System Settings, Privacy & Security, scroll to FileVault, Turn On. Choose to create a recovery key and keep it somewhere other than this Mac (a password manager, or printed and locked away).

What this means day to day: after a power cut or a restart, the Mac stops at its unlock screen until someone types the password at the Mac itself. Screen Sharing cannot do that step. For a planned restart, run `sudo fdesetup authrestart` in Terminal instead of choosing Restart; it unlocks once for that boot.

**Check:** System Settings, Privacy & Security shows "FileVault is turned on for the disk".

### A3. Keep it awake and coming back

System Settings, Energy: turn on "Prevent automatic sleeping when the display is off", "Wake for network access" and "Start up automatically after a power failure". Or in Terminal (Applications, Utilities, Terminal):

```
sudo pmset -a sleep 0 disksleep 0 womp 1 autorestart 1
```

**Check:** `pmset -g` lists `sleep 0` and `autorestart 1`.

### A4. Firewall and Tailscale

1. System Settings, Network, Firewall: on.
2. Install Tailscale from tailscale.com/download (or the App Store), open it, and log in.
3. Click the Tailscale icon in the menu bar. It shows this Mac's name (for example `mac-mini`) and its 100.x.y.z address. Write the name down.

**Check:** the Tailscale menu says Connected.

### A5. Screen Sharing

1. System Settings, General, Sharing: turn on Screen Sharing.
2. Click the (i) next to it. Set "Allow access for" to "Only these users" and keep only your account.
3. Install Tailscale on the computer you will connect from, logged in to the same Tailscale account.
4. Connect from that computer:
   - From a Mac: Finder, Go, Connect to Server, type `vnc://mac-mini` (the name from A4), and log in with your Mac account.
   - From Windows: install a VNC viewer (RealVNC Viewer works), connect to `mac-mini`. macOS also needs "VNC viewers may control screen with password" turned on in the (i) panel for this, with its own password.

Never forward a port on your router to the Mac. Tailscale is the only way in.

**Check:** you see the Mac's screen from the other computer.

### A6. OneDrive

1. Install OneDrive from the App Store and sign in with your university account. Accept the default folder location.
2. In Finder, open the OneDrive folder and find the project folder that holds (or will hold) the case workbook.
3. Right-click the project folder and choose Always Keep on This Device. A cloud-only placeholder cannot be read by the planner.
4. Once the workbook is there (Part D does this if you don't have it yet), right-click it, hold the Option key, and choose Copy "…" as Pathname. Paste it somewhere for step A14. It looks like `/Users/you/Library/CloudStorage/OneDrive-YourUniversity/Stone/stone-planner-cases.xlsx`.

**Check:** the project folder's files show a solid circle (kept on this device), not a cloud.

### A7. Homebrew

Homebrew installs the developer tools. In Terminal:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It asks for your Mac password and may install Apple's command line tools first, which takes a few minutes. When it finishes it prints "Next steps" with two commands that add `brew` to your path; copy and run them exactly as printed.

**Check:** close Terminal, open a new one, and `brew --version` prints a version.

### A8. Python, Node and git

```
brew install python@3.13 node git
```

**Check:**

```
python3.13 --version
node --version
git --version
```

Python should say 3.13.x and Node v22.12 or newer.

### A9. The planner

```
cd ~
git clone https://github.com/urologai/stone-planner.git
cd stone-planner
python3.13 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[web]"
```

**Check:**

```
.venv/bin/python -c "import stoneplanner_api.app; print('planner ok')"
.venv/bin/dcm2niix -h | head -1
```

The first prints `planner ok`; the second starts with `Chris Rorden's dcm2niiX`.

Optional: to let the Mac segment on its own processor too (slow, but needs no PC), also run `.venv/bin/pip install -e ".[web,segment]"`. That adds PyTorch and TotalSegmentator, several GB. You can do this later.

### A10. Build the web page

```
cd ~/stone-planner/frontend
npm ci
npm run build
cd ..
```

**Check:** `ls frontend/dist` lists `index.html` and an `assets` folder.

### A11. Folders and the settings file

```
mkdir -p ~/stone-data ~/stone-key ~/stone-report
chmod 700 ~/stone-key
```

- `~/stone-data`: de-identified case folders and `manual.csv`.
- `~/stone-key`: the MRN key file, the Admin tab's saved tokens, and the settings file below. Keep it out of OneDrive and every other synced folder.

Now create the settings file. Pick the planner's login password first: letters, digits and dashes only (no spaces, quotes or `$`), and long. This asks for it without showing it:

```
cd ~/stone-key
read -s "PW?Planner password: "; echo
cat > stoneplanner.env <<EOF
APP_PASSWORD=$PW
SECRET_KEY=$(openssl rand -hex 32)
COOKIE_SECURE=false
DATA_DIR=$HOME/stone-data
KEY_FILE=$HOME/stone-key/mrn_key.csv
EOF
chmod 600 stoneplanner.env
unset PW
cd ~
```

`SECRET_KEY` keeps you logged in across restarts. `COOKIE_SECURE=false` is correct here: the browser and the planner are on the same machine, over plain HTTP.

**Check:** `ls -l ~/stone-key/stoneplanner.env` starts with `-rw-------`, and `cat ~/stone-key/stoneplanner.env` shows five lines with your password and a long hex key.

### A12. First start, by hand

```
~/stone-planner/scripts/run_local.sh
```

It prints a few lines ending in `Uvicorn running on http://127.0.0.1:8000`. Open Safari on the Mac and go to http://localhost:8000. Log in with the password from A11.

**Check:** you see the tabs Ingest, Planner and Admin. Go back to Terminal and press Control-C to stop it.

### A13. Start it automatically

This writes the launch agent with your paths filled in, then starts it:

```
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
cat > ~/Library/LaunchAgents/org.stoneplanner.app.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>org.stoneplanner.app</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>$HOME/stone-planner/scripts/run_local.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/stoneplanner.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/stoneplanner.log</string>
</dict>
</plist>
EOF
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.stoneplanner.app.plist
```

It now starts whenever you are logged in and restarts itself if it stops. With FileVault on, unlocking the Mac after a restart logs you in, which starts it.

**Check:**

```
sleep 5; curl -s http://127.0.0.1:8000/api/health
```

prints `{"ok":true,"auth_configured":true,...}`. If not, read the log: `tail -50 ~/Library/Logs/stoneplanner.log`.

Stop it with `launchctl bootout gui/$(id -u)/org.stoneplanner.app`; start it again with the `launchctl bootstrap` line above.

### A14. Link the workbook

In the browser on the Mac (http://localhost:8000), Admin tab, Case workbook: paste the path from A6, Save, then Check workbook.

**Check:** it reports the number of case rows. If the workbook is still blank, it says 0 case rows; that is fine.

If it says permission denied, macOS is keeping the background planner out of the OneDrive folder. Try System Settings, Privacy & Security, Files and Folders, and allow the entry for the planner if one appears. If none does, run the planner from a Terminal window instead of the launch agent: `launchctl bootout gui/$(id -u)/org.stoneplanner.app`, then `~/stone-planner/scripts/run_local.sh` and leave that window open. This part has not been tested on a Mac yet; tell me what you see.

### A15. Segmentation, for now

Until the PC is ready, leave the Admin tab's Segmentation on Off, or on This computer if you did the optional install in A9. Part C switches it to the PC.

---

## Part B: the PC with the RTX 4070 Ti

Do Part B at the PC, in PowerShell (Start, type PowerShell). Steps that need an administrator PowerShell say so: right-click PowerShell and choose Run as administrator.

### B1. Update Windows

Settings, Windows Update: install everything and restart.

### B2. NVIDIA driver 580 or newer

Install the current driver from the NVIDIA App or nvidia.com (Game Ready or Studio, either works).

**Check:**

```
nvidia-smi
```

The top of the table shows `NVIDIA GeForce RTX 4070 Ti`, a Driver Version of 580 or higher, and `CUDA Version: 13.0` or higher. If CUDA Version is below 13.0, the driver is too old for the worker.

### B3. WSL

In an administrator PowerShell:

```
wsl --install
```

Restart when asked. After the restart an Ubuntu window may open and ask for a user name and password; pick anything and remember it.

**Check:** `wsl --status` says the default version is 2.

### B4. Docker Desktop

1. Download Docker Desktop for Windows from docker.com and run the installer. Keep "Use WSL 2 instead of Hyper-V" ticked.
2. Start Docker Desktop and accept the terms. A Docker account is not needed; skip sign-in.
3. Docker Desktop, Settings, General: turn on "Start Docker Desktop when you sign in to your computer".

**Check:** `docker version` shows both a Client and a Server section.

### B5. Git

```
winget install --id Git.Git -e --source winget
```

Close PowerShell and open a new one.

**Check:** `git --version` prints a version.

### B6. Tailscale

Install Tailscale for Windows from tailscale.com/download and log in to the same account as the Mac.

**Check:**

```
tailscale ip -4
tailscale status
```

The first prints this PC's 100.x.y.z address; the first line of the second shows this PC's name (for example `home-pc`). Write both down.

### B7. No sleep while serving

Settings, System, Power: set "When plugged in, put my device to sleep after" to Never. The planner does not switch to Modal by itself when the PC is asleep.

### B8. Build the worker

```
cd $HOME
git clone https://github.com/urologai/stone-planner.git
cd stone-planner
docker build -f gpu_worker/Dockerfile -t stone-planner-gpu .
```

This downloads several GB (PyTorch with its CUDA libraries) and takes a while.

**Check:**

```
docker run --rm --gpus all stone-planner-gpu python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

prints `True NVIDIA GeForce RTX 4070 Ti`. If it prints `False` or errors, the driver (B2) or Docker's WSL setup (B4) is the problem.

### B9. Make the worker token

A random 64-character string that the Mac will send with every request:

```
wsl openssl rand -hex 32
```

Copy it into a password manager. You need it in B10 and in Part C.

### B10. Run the worker

Replace the address with this PC's Tailscale address from B6, and the token with the one from B9:

```
docker run -d --name stone-gpu --restart unless-stopped --gpus all `
  -p 100.x.y.z:8100:8100 `
  -e WORKER_TOKEN=paste-the-token `
  -v stoneplanner-weights:/weights `
  stone-planner-gpu
```

Binding to the Tailscale address means only machines on your Tailscale account can reach the worker, not the rest of the home network.

**Check:**

```
docker ps
docker logs stone-gpu
```

`docker ps` lists `stone-gpu` with status Up; the log ends with `Uvicorn running on http://0.0.0.0:8100`.

If `docker run` fails with an error about the address, Docker could not bind to the Tailscale address. Remove the container (`docker rm -f stone-gpu`) and run the same command with `-p 8100:8100` instead. Then, in an administrator PowerShell, allow the port only from Tailscale's address range:

```
New-NetFirewallRule -DisplayName "stone-planner GPU worker (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 8100 -RemoteAddress 100.64.0.0/10 -Action Allow
```

### B11. Reach the worker from the Mac

On the Mac (over Screen Sharing), in Terminal, with the PC's name from B6 and the token from B9:

```
curl -s -H "Authorization: Bearer paste-the-token" http://home-pc:8100/health
```

**Check:** it prints JSON with `"gpu":"NVIDIA GeForce RTX 4070 Ti"` and `"gpu_memory_gb"` near 12. `{"detail":"wrong or missing token"}` means the token does not match. No answer at all means a network problem: both machines connected in Tailscale, the worker running (`docker ps` on the PC), and the name spelled as `tailscale status` shows it.

### B12. After the PC restarts

Docker Desktop starts at sign-in and restarts the worker. If the worker had been bound to the Tailscale address and Tailscale was not connected yet, it may fail to start. Check with `docker ps`; if `stone-gpu` is missing, run `docker start stone-gpu` once Tailscale is connected.

---

## Part C: connect them

### C1. Point the planner at the PC

In the browser on the Mac, Admin tab, Segmentation:

1. Choose Home PC GPU.
2. GPU worker address: `http://home-pc:8100`, with the PC's name from B6 (or `http://100.x.y.z:8100`).
3. Worker token: the token from B9.
4. Save, then Check connection.

**Check:** "GPU worker answered: NVIDIA GeForce RTX 4070 Ti, 12 GB". The token box now says "saved; type to replace"; the planner never shows it again.

### C2. Test segmentation

Press Test segmentation. The first run downloads the model weights (about 1.6 GB) to the PC, so it takes several minutes. Run it again once it finishes.

**Check:** "Test done … s, peak graphics memory … GB. … labels in the result, on the grid of the CT." With no cases ingested yet, the test uses a small synthetic phantom, so the time and memory are not representative; Part D repeats the test on a real CT.

### C3. Modal instead (optional)

To use the cloud GPU instead, follow [deploy.md](deploy.md), section 1, to deploy the Modal function and make a token. Then choose Cloud GPU (Modal) on the Admin tab, enter the token ID and secret, Save and Check connection. You can switch between the PC and Modal at any time.

---

## Part D: the first real case

### D1. The workbook

If the team does not have the workbook yet: Ingest tab, Download blank workbook, and save it into the OneDrive project folder from A6. If its path differs from what you set in A14, update the Admin tab.

In the workbook: on the Key sheet, type the patient's MRN next to `case_001`. On the Cases sheet, choose `case_001` in row 3 and fill the yellow cells until Still needed says complete. Save, and give OneDrive a moment to sync.

### D2. Export the CT

From PACS, export the preoperative noncontrast CT study as DICOM to a folder on the Mac, for example `~/Desktop/export-case_001`. The whole study is fine; the planner picks the axial series.

### D3. Ingest

Ingest tab:

1. Load linked workbook. Row `case_001` should say ready.
2. Fill form on that row.
3. CT folder: choose the export folder. Check that the marked series is the axial CT and note the slice thickness.
4. Add the postop CT or KUB folder if there is one.
5. Ingest case, and read the message.

**Check:** `case_001` appears under Cases on this computer.

### D4. Delete the export

The export folder holds identifiable DICOM. Once the case is ingested, drag it to the Trash and empty the Trash.

### D5. The real test

Admin tab, Test segmentation: it now uses `case_001`'s de-identified CT. Note the time and the peak graphics memory. On a 12 GB card the peak should stay well under 12 GB for a typical abdominal CT; if a case runs out of memory, send that one to Modal.

### D6. Plan it

Ingest tab, Open in planner on `case_001`, then Segment, then Plan tracts, then Save to case folder.

**Check:** the top three tracts show on the CT in green, yellow and orange, with the ranked table below.

### D7. Tell me

Send me the time and peak graphics memory from D5 and anything that went wrong, with the exact message.

---

## Troubleshooting

| Symptom | What to do |
|---|---|
| http://localhost:8000 does not load on the Mac | `curl -s http://127.0.0.1:8000/api/health`. If nothing answers, `tail -50 ~/Library/Logs/stoneplanner.log` and `launchctl print gui/$(id -u)/org.stoneplanner.app \| grep state`. After a power cut, someone has to unlock the Mac at the Mac (FileVault). |
| The log says the env file must be readable only by you | `chmod 600 ~/stone-key/stoneplanner.env` |
| Check workbook: no file at … | The path on the Admin tab is wrong, or OneDrive has not synced the file yet. Copy the path again (A6). |
| Check workbook: permission denied | See A14. |
| Ingest says dcm2niix is missing or SimpleITK is not installed | `cd ~/stone-planner && .venv/bin/pip install -e ".[web]"`, or `brew install dcm2niix`, then `launchctl kickstart -k gui/$(id -u)/org.stoneplanner.app`. |
| Check connection: not reachable | On the PC, `docker ps` shows `stone-gpu` Up; both machines show Connected in Tailscale; the address matches `tailscale status`. Then B11 from the Mac. |
| Check connection: 401 | The token on the Admin tab does not match the PC's. Type it again and Save. |
| Check connection names no GPU | The worker started without the GPU: run the B8 check, then recreate the container with `--gpus all`. |
| Test fails with a CUDA out-of-memory message | That scan is too large for 12 GB. Close anything else using the card (games), try again, or switch that case to Modal. |
| Worker gone after the PC restarted | `docker start stone-gpu` once Tailscale is connected (B12). |

## Updating later

Mac:

```
cd ~/stone-planner
git pull
.venv/bin/pip install -e ".[web]"
cd frontend && npm ci && npm run build && cd ..
launchctl kickstart -k gui/$(id -u)/org.stoneplanner.app
```

PC:

```
cd $HOME\stone-planner
git pull
docker build -f gpu_worker/Dockerfile -t stone-planner-gpu .
docker rm -f stone-gpu
```

then the B10 command again. The weights volume survives, so there is no new download.
