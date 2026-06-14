#!/usr/bin/env bash
#
# Remove the AI service systemd deployment from a Raspberry Pi.
# Run this ON THE PI:
#
#     sudo bash deploy/uninstall_service.sh
#
# Infra-only by design: it stops + disables the service and removes the systemd
# unit and journald drop-in. It KEEPS your data — .env, logs/, data/app.db and
# the .venv are left untouched, so you can reinstall later without re-setup.

set -euo pipefail

SERVICE_NAME="ai-service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
JOURNALD_DROPIN="/etc/systemd/journald.conf.d/${SERVICE_NAME}.conf"

# Project root = parent of this script's deploy/ directory (for the "kept" notice).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Must be root (touches /etc and manages systemd) -------------------------
if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash deploy/uninstall_service.sh" >&2
    exit 1
fi

# --- Stop + disable the service (ignore if already gone) ---------------------
# Don't let a missing/inactive unit abort the script (set -e).
systemctl stop    "${SERVICE_NAME}.service" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true
echo "Service stopped and disabled."

# --- Remove the unit + journald drop-in --------------------------------------
removed_any=false
if [[ -f "${UNIT_DST}" ]]; then
    rm -f "${UNIT_DST}"
    echo "Removed ${UNIT_DST}"
    removed_any=true
fi
if [[ -f "${JOURNALD_DROPIN}" ]]; then
    rm -f "${JOURNALD_DROPIN}"
    echo "Removed ${JOURNALD_DROPIN}"
    systemctl restart systemd-journald
    removed_any=true
fi
if [[ "${removed_any}" == "false" ]]; then
    echo "No installed unit/journald files found — nothing to remove."
fi

systemctl daemon-reload
systemctl reset-failed "${SERVICE_NAME}.service" 2>/dev/null || true

echo
echo "Deployment removed. Kept (not deleted):"
echo "  ${APP_DIR}/.env"
echo "  ${APP_DIR}/logs/"
echo "  ${APP_DIR}/data/   (SQLite DB)"
echo "  ${APP_DIR}/.venv/"
echo
echo "Reinstall any time with:  sudo bash deploy/install_service.sh"
