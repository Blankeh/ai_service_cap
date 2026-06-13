# Running the AI service on boot (Raspberry Pi 4)

This folder makes the AI service start automatically when the Pi boots, restart
itself if it ever crashes, and keep logs that survive reboots — all from one
command.

## One-time setup on the Pi

```bash
# 1. Get the code onto the Pi (e.g. ~/ai_service_cap) and create your .env:
cd ~/ai_service_cap
nano .env          # set CLOUDFLARE_API_KEY, BUS_ID, APP_ENV=prod, etc.

# 2. Run the installer — it does everything:
sudo bash deploy/install_service.sh
```

The installer automatically:

1. **Installs system prerequisites** — detects the package manager
   (apt / dnf / yum / zypper / pacman) and installs python3, the venv module,
   and pip if any are missing.
2. **Creates the virtualenv** (`.venv`) and installs dependencies if not present
   — CPU-only PyTorch (the default aarch64 torch wheel would drag in ~2 GB of
   useless NVIDIA CUDA libs), with pip's temp files staged on disk instead of
   the Pi's tiny RAM-backed `/tmp` (avoids the `No space left on device` error).
3. **Configures the journal** to be persistent across reboots and capped at
   200 MB so it never fills the SD card.
4. **Installs, enables, and starts** the systemd unit — auto-detecting the
   project path, the user to run as, and the venv's Python.

It's idempotent: re-run it any time after a code or `.env` change.

## Managing the service

```bash
sudo systemctl status ai-service     # current state
sudo systemctl restart ai-service    # restart after a code/.env change
sudo systemctl stop ai-service       # stop
sudo systemctl disable ai-service    # don't start on boot
journalctl -u ai-service -f          # live logs
journalctl -u ai-service -b          # logs since last boot
```

## Notes

- `WorkingDirectory` is the project root because `.env`, the SQLite database
  (`data/app.db`) and the `logs/` directory are all resolved relative to it.
- The unit waits for `network-online.target` since the service reaches out to
  Cloudflare and broadcasts UDP camera-sync triggers on the LAN.
- The app also writes rotating file logs to `logs/` (5 MB × 3 backups),
  independent of the journal.
- For production, set `APP_ENV=prod` in `.env` so the `/dev` viewer is never
  exposed.
