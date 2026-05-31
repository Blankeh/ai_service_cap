"""
Database management CLI for the Bus Crowd AI Service.

Usage (run from ai_service/):
    python -m scripts.db_cli init
    python -m scripts.db_cli cameras list
    python -m scripts.db_cli cameras add  <camera_id> --bus <bus_id> --pane <pane>
    python -m scripts.db_cli cameras assign <camera_id> --bus <bus_id> --pane <pane>
    python -m scripts.db_cli queue list
    python -m scripts.db_cli queue clear
"""

import argparse
import sys
import os

# Allow running from ai_service/ without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.core.config import settings
from src.repos.database import init_engine
from src.repos.camera_repo import CameraRepo
from src.repos.queue_repo import QueueRepo


def get_repos():
    init_engine(settings.database_url)
    return CameraRepo(), QueueRepo()


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_init(args):
    camera_repo, queue_repo = get_repos()
    print(f"[OK] cameras DB : {settings.camera_db_path}")
    print(f"[OK] queue DB   : {settings.queue_db_path}")
    print("Both databases initialised.")


def cmd_cameras_list(args):
    camera_repo, _ = get_repos()
    rows = camera_repo.list_all()
    if not rows:
        print("No cameras registered yet.")
        return

    fmt = "{:<22} {:<10} {:<8} {:<10} {}"
    print(fmt.format("CAMERA ID", "BUS ID", "PANE", "ASSIGNED", "LAST SEEN"))
    print("-" * 70)
    for r in rows:
        assigned = "yes" if r.bus_id else "no"
        print(fmt.format(
            r.camera_id,
            r.bus_id   or "-",
            r.pane     or "-",
            assigned,
            r.last_seen[:19],
        ))


def cmd_cameras_add(args):
    camera_repo, _ = get_repos()
    existing = camera_repo.get_by_camera_id(args.camera_id)
    if existing:
        print(f"Camera {args.camera_id!r} already registered — use 'assign' to update.")
        return

    camera_repo.register_or_touch(args.camera_id)

    if args.bus and args.pane:
        camera_repo.assign(args.camera_id, args.bus, args.pane)
        print(f"[OK] Registered {args.camera_id} → bus={args.bus} pane={args.pane}")
    else:
        print(f"[OK] Registered {args.camera_id} (unassigned — run 'assign' to set bus/pane)")


def cmd_cameras_assign(args):
    camera_repo, _ = get_repos()
    if not camera_repo.get_by_camera_id(args.camera_id):
        print(f"Camera {args.camera_id!r} not found. Register it first with 'cameras add'.")
        sys.exit(1)
    camera_repo.assign(args.camera_id, args.bus, args.pane)
    print(f"[OK] {args.camera_id} → bus={args.bus} pane={args.pane}")


def cmd_queue_list(args):
    _, queue_repo = get_repos()
    rows = queue_repo.get_due()
    total = queue_repo.count()
    print(f"Pending records: {total}")
    if not rows:
        return

    fmt = "{:<5} {:<22} {:<10} {:<8} {:<6} {:<5} {}"
    print(fmt.format("ID", "CAMERA ID", "BUS ID", "PANE", "COUNT", "RETRY", "TIMESTAMP"))
    print("-" * 80)
    for r in rows:
        print(fmt.format(
            r.id,
            r.camera_id,
            r.bus_id,
            r.pane,
            r.crowd_count,
            r.retry_count,
            r.timestamp[:19],
        ))


def cmd_queue_clear(args):
    _, queue_repo = get_repos()
    rows = queue_repo.get_due()
    for r in rows:
        queue_repo.delete(r["id"])
    print(f"[OK] Cleared {len(rows)} record(s) from queue.")


# ── Argument parser ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bus Crowd AI Service — DB management")
    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="Initialise both databases")

    # cameras
    cam_parser = sub.add_parser("cameras", help="Camera management")
    cam_sub = cam_parser.add_subparsers(dest="cameras_command")

    cam_sub.add_parser("list", help="List all registered cameras")

    add_parser = cam_sub.add_parser("add", help="Register a new camera")
    add_parser.add_argument("camera_id", help="Camera ID (e.g. MAC address AA:BB:CC:DD:EE:FF)")
    add_parser.add_argument("--bus",  help="Bus ID to assign (optional)")
    add_parser.add_argument("--pane", help="Pane to assign (optional)")

    asgn_parser = cam_sub.add_parser("assign", help="Assign a camera to a bus/pane")
    asgn_parser.add_argument("camera_id")
    asgn_parser.add_argument("--bus",  required=True)
    asgn_parser.add_argument("--pane", required=True)

    # queue
    q_parser = sub.add_parser("queue", help="Failsafe queue management")
    q_sub = q_parser.add_subparsers(dest="queue_command")
    q_sub.add_parser("list",  help="List pending records")
    q_sub.add_parser("clear", help="Delete all pending records")

    args = parser.parse_args()

    dispatch = {
        ("init",    None):        cmd_init,
        ("cameras", "list"):      cmd_cameras_list,
        ("cameras", "add"):       cmd_cameras_add,
        ("cameras", "assign"):    cmd_cameras_assign,
        ("queue",   "list"):      cmd_queue_list,
        ("queue",   "clear"):     cmd_queue_clear,
    }

    sub_cmd = getattr(args, "cameras_command", None) or getattr(args, "queue_command", None)
    fn = dispatch.get((args.command, sub_cmd))

    if fn is None:
        parser.print_help()
        sys.exit(1)

    fn(args)


if __name__ == "__main__":
    main()
