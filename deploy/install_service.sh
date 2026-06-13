#!/usr/bin/env bash
#
# Turnkey installer for the AI service on a Raspberry Pi 4.
# Run this ON THE PI from inside the project:
#
#     cd ~/ai_service_cap
#     sudo bash deploy/install_service.sh
#
# It does EVERYTHING automatically:
#   1. Creates a virtualenv (.venv) and installs requirements if missing.
#   2. Configures the systemd journal to survive reboots and stay size-capped
#      (protects the SD card).
#   3. Renders + installs the systemd unit, then enables it to start on boot
#      and starts it now.
# The script is idempotent — safe to re-run after a code or .env change.

set -euo pipefail

SERVICE_NAME="ai-service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
JOURNALD_DROPIN="/etc/systemd/journald.conf.d/${SERVICE_NAME}.conf"

# --- Must be root (writes to /etc and manages systemd) -----------------------
if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash deploy/install_service.sh" >&2
    exit 1
fi

# --- Resolve paths -----------------------------------------------------------
# Project root = parent of this script's deploy/ directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
UNIT_SRC="${SCRIPT_DIR}/ai-service.service"

# Run as the user who owns the project (not root). When invoked via sudo,
# SUDO_USER is that login user.
RUN_USER="${SUDO_USER:-$(stat -c '%U' "${APP_DIR}")}"

echo "Project dir : ${APP_DIR}"
echo "Run as user : ${RUN_USER}"

# --- 0. Ensure system prerequisites (python3 + venv + pip) -------------------
# Detect the distro's package manager and install the packages that provide
# python3, the venv module, and pip. Package names differ per family.
install_prereqs() {
    local pm pkgs
    if   command -v apt-get >/dev/null 2>&1; then
        pm="apt-get"; pkgs=(python3 python3-venv python3-pip)
        apt-get update -y
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        pm="dnf"; pkgs=(python3 python3-pip)   # venv ships with python3
        dnf install -y "${pkgs[@]}"
    elif command -v yum >/dev/null 2>&1; then
        pm="yum"; pkgs=(python3 python3-pip)
        yum install -y "${pkgs[@]}"
    elif command -v zypper >/dev/null 2>&1; then
        pm="zypper"; pkgs=(python3 python3-pip)
        zypper --non-interactive install "${pkgs[@]}"
    elif command -v pacman >/dev/null 2>&1; then
        pm="pacman"; pkgs=(python python-pip)
        pacman -Sy --noconfirm "${pkgs[@]}"
    else
        echo "ERROR: no supported package manager found (apt/dnf/yum/zypper/pacman)." >&2
        echo "Install python3, the venv module, and pip manually, then re-run." >&2
        exit 1
    fi
    echo "Installed prerequisites via ${pm}: ${pkgs[*]}"
}

# Only touch the system if something is actually missing.
if ! command -v python3 >/dev/null 2>&1 \
   || ! python3 -m venv --help >/dev/null 2>&1 \
   || ! python3 -m pip --version  >/dev/null 2>&1; then
    echo "Installing missing system prerequisites ..."
    install_prereqs
else
    echo "System prerequisites present (python3 + venv + pip)."
fi

# --- 1. Virtualenv + dependencies (auto-create if missing) -------------------
# Notes for the Pi (ARM, no GPU):
#  * Install CPU-only PyTorch. The default aarch64 torch wheel declares ~2 GB of
#    NVIDIA CUDA deps (cuDNN etc.) that are useless here and blow up the install.
#  * Stage pip's temp files on disk: /tmp is a small RAM-backed tmpfs on Pi OS,
#    too small for the torch wheels, so a default install fails with ENOSPC.
VENV_DIR="${APP_DIR}/.venv"
PIP_TMP="${APP_DIR}/.pip_tmp"
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
PYTHON="${VENV_DIR}/bin/python"

# Create the venv if it isn't there yet (owned by the project user, not root).
if [[ ! -x "${PYTHON}" ]]; then
    echo "Creating virtualenv at ${VENV_DIR} ..."
    sudo -u "${RUN_USER}" python3 -m venv "${VENV_DIR}"
fi

# Always verify the deps are actually importable — a venv can exist but be empty
# or half-built (e.g. an earlier install ran out of space). Install only if
# something is missing, so a complete venv is a no-op and re-runs stay fast.
if sudo -u "${RUN_USER}" "${PYTHON}" -c "import uvicorn, fastapi, ultralytics, torch, cv2" 2>/dev/null; then
    echo "Dependencies present."
else
    echo "Installing dependencies (CPU-only torch; pip temp staged on disk) ..."
    sudo -u "${RUN_USER}" mkdir -p "${PIP_TMP}"
    sudo -u "${RUN_USER}" env TMPDIR="${PIP_TMP}" \
        "${VENV_DIR}/bin/pip" install --upgrade pip
    # CPU-only torch first — its wheel pulls no nvidia-* packages (the default
    # aarch64 torch would drag in ~2 GB of useless CUDA libs).
    sudo -u "${RUN_USER}" env TMPDIR="${PIP_TMP}" \
        "${VENV_DIR}/bin/pip" install torch torchvision --index-url "${TORCH_CPU_INDEX}"
    # Rest of the deps — torch is already satisfied, so no CUDA gets pulled.
    sudo -u "${RUN_USER}" env TMPDIR="${PIP_TMP}" \
        "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"
    rm -rf "${PIP_TMP}"
    # Hard fail if deps still aren't importable — don't enable a broken service.
    if ! sudo -u "${RUN_USER}" "${PYTHON}" -c "import uvicorn, fastapi, ultralytics, torch, cv2" 2>/dev/null; then
        echo "ERROR: dependency install did not complete — check pip output above." >&2
        exit 1
    fi
fi
echo "Python      : ${PYTHON}"

# Is the service actually configured? Only START it now if .env exists; either
# way it gets enabled so it comes up on the next boot once configured. This
# avoids launching a guaranteed crash-loop on a half-set-up Pi.
HAVE_ENV=true
if [[ ! -f "${APP_DIR}/.env" ]]; then
    HAVE_ENV=false
    echo "WARNING: ${APP_DIR}/.env not found — will enable on boot but NOT start now." >&2
fi

# --- 2. Persistent, size-capped journal --------------------------------------
# Default Pi journal is volatile (RAM) and wiped on reboot. Make it persistent
# and cap disk usage so logs survive reboots without filling the SD card.
mkdir -p /var/log/journal
mkdir -p "$(dirname "${JOURNALD_DROPIN}")"
cat > "${JOURNALD_DROPIN}" <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=200M
EOF
systemctl restart systemd-journald
echo "Journal configured -> persistent, capped at 200M (${JOURNALD_DROPIN})"

# --- 3. Render, install, enable + start the unit -----------------------------
sed -e "s|__USER__|${RUN_USER}|g" \
    -e "s|__APP_DIR__|${APP_DIR}|g" \
    -e "s|__PYTHON__|${PYTHON}|g" \
    "${UNIT_SRC}" > "${UNIT_DST}"
echo "Installed unit -> ${UNIT_DST}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

if [[ "${HAVE_ENV}" == "true" ]]; then
    systemctl restart "${SERVICE_NAME}.service"
    echo
    systemctl --no-pager --full status "${SERVICE_NAME}.service" || true
    echo
    echo "All set. The service is running and will auto-start on every boot."
    echo "Follow logs with:  journalctl -u ${SERVICE_NAME} -f"
else
    echo
    echo "Service ENABLED for boot but NOT started (no .env yet)."
    echo "Create ${APP_DIR}/.env, then:  sudo systemctl start ${SERVICE_NAME}"
fi
