#!/usr/bin/env python3
"""
Odoo development environment CLI.

Manages the Odoo process inside the running Docker container.
All commands communicate with the 'odoo' service via `docker compose exec`.

Usage:
    python odoo-cli.py <command> [options]

Run `python odoo-cli.py --help` for the full command list.
"""

import argparse
import os
import pathlib
import subprocess
import sys
import time

# ── constants ────────────────────────────────────────────────────────────────

# Ensure docker compose runs from the project root regardless of CWD
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

COMPOSE = ["docker", "compose", "--project-directory", PROJECT_DIR]
SERVICE = "odoo"

PID_FILE = "/tmp/odoo.pid"
# Log written inside the rw-mounted volume so it's readable from the host
# at ./odoo/auto/odoo.log
LOG_FILE = "/opt/odoo/auto/odoo.log"

DEFAULT_FLAGS = [
    "--workers=0",
    "--dev=reload,qweb,werkzeug,xml",
    "--limit-memory-soft=0",
    "--limit-time-real=9999999",
    "--limit-time-real-cron=9999999",
]

# ── helpers ──────────────────────────────────────────────────────────────────


def _exec(cmd, *, interactive=False, check=True, capture=False, **kwargs):
    """Run *cmd* inside the odoo container via docker compose exec."""
    tty_flags = ["-it"] if interactive else ["-T"]
    full = COMPOSE + ["exec"] + tty_flags + [SERVICE] + cmd
    if capture:
        return subprocess.run(
            full, check=check, text=True, capture_output=True, **kwargs
        )
    return subprocess.run(full, check=check, **kwargs)


def _is_running():
    """Return True if an Odoo process is alive inside the container."""
    r = _exec(
        ["bash", "-c", f"[ -f {PID_FILE} ] && kill -0 $(cat {PID_FILE}) 2>/dev/null"],
        check=False,
    )
    return r.returncode == 0


def _container_running():
    """Return True if the odoo container itself is up."""
    r = subprocess.run(
        COMPOSE + ["ps", "--status", "running", "--services"],
        check=False,
        text=True,
        capture_output=True,
    )
    return SERVICE in r.stdout.splitlines()


# ── commands ─────────────────────────────────────────────────────────────────


def cmd_start(args):
    if not _container_running():
        print(
            "ERROR: The odoo container is not running. Run: docker compose up -d",
            file=sys.stderr,
        )
        sys.exit(1)
    if _is_running():
        print("Odoo is already running. Use 'restart' to restart it.")
        sys.exit(0)

    flags = list(DEFAULT_FLAGS)
    if args.database:
        flags += ["-d", args.database]
    if getattr(args, "install", None):
        flags += ["-i", ",".join(args.install)]
    if getattr(args, "update", None):
        flags += ["-u", ",".join(args.update)]

    odoo_cmd = "odoo " + " ".join(flags)
    launch = (
        f"nohup {odoo_cmd} > {LOG_FILE} 2>&1 & "
        f"echo $! > {PID_FILE} && "
        f'echo "Odoo started (PID $(cat {PID_FILE})). Logs: {LOG_FILE}"'
    )
    _exec(["bash", "-c", launch])


def cmd_stop(args):
    if not _is_running():
        print("Odoo is not running.")
        return
    _exec(["bash", "-c", f"kill $(cat {PID_FILE}) && rm -f {PID_FILE}"])
    print("Odoo stopped.")


def cmd_restart(args):
    if _is_running():
        _exec(["bash", "-c", f"kill $(cat {PID_FILE}) && rm -f {PID_FILE}"])
        print("Stopped. Waiting for process to exit...")
        time.sleep(2)
    cmd_start(args)


def cmd_status(args):
    if not _container_running():
        print("Container: not running")
        return
    print("Container: running")
    if _is_running():
        r = _exec(["cat", PID_FILE], capture=True, check=False)
        pid = r.stdout.strip()
        print(f"Odoo:      running (PID {pid})")
        print(f"Logs:      {LOG_FILE}  (host path: ./odoo/auto/odoo.log)")
    else:
        print("Odoo:      not running")


def cmd_logs(args):
    if not _container_running():
        print("ERROR: Container is not running.", file=sys.stderr)
        sys.exit(1)
    n = args.lines if args.lines else 100
    flags = f"-n {n}" + (" -f" if args.follow else "")
    no_log_msg = "No log file yet — start Odoo first."
    bash = f"tail {flags} {LOG_FILE} 2>/dev/null || echo '{no_log_msg}'"
    _exec(["bash", "-c", bash], interactive=args.follow)


def cmd_pip(args):
    if not _container_running():
        print("ERROR: Container is not running.", file=sys.stderr)
        sys.exit(1)
    _exec(["pip", "install", "--no-cache-dir"] + args.packages)
    print(
        "\nNote: this install is ephemeral (lost on container recreate).\n"
        "For persistence, add the package to odoo/custom/src/odoo_requirements.txt\n"
        "and run: docker compose build"
    )


def cmd_shell(args):
    if not _container_running():
        print("ERROR: Container is not running.", file=sys.stderr)
        sys.exit(1)
    db_flag = ["-d", args.database] if args.database else []
    _exec(["odoo", "shell"] + db_flag, interactive=True)


def cmd_exec(args):
    """Run an arbitrary command inside the container."""
    if not args.cmd:
        print("ERROR: Provide a command to run.", file=sys.stderr)
        sys.exit(1)
    _exec(args.cmd, interactive=sys.stdin.isatty())


def _rel(path):
    """Return *path* relative to PROJECT_DIR for display."""
    return pathlib.Path(path).relative_to(PROJECT_DIR)


def _collect_desired(addons_paths_dir):
    """Return ({name: module_path}, error_count) from all addons_paths/*.txt."""
    desired: dict[str, pathlib.Path] = {}
    errors = 0
    config_files = sorted(
        f
        for f in addons_paths_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
    )
    for addons_file in config_files:
        for raw in addons_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            module_path = (pathlib.Path(PROJECT_DIR) / line).resolve()
            if not module_path.exists():
                src = _rel(addons_file)
                print(f"  MISSING  {module_path}  (from {src})")
                errors += 1
                continue
            name = module_path.name
            if name in desired:
                if desired[name] != module_path:
                    a, b = _rel(module_path), _rel(desired[name])
                    print(f"  CONFLICT {name}: {a} vs {b} — skipping")
                    errors += 1
                continue
            desired[name] = module_path
    return desired, errors


def _apply_symlinks(desired, private_dir, dry_run):
    """Create symlinks in *private_dir* for each entry in *desired*.

    Returns (created, skipped, errors).
    """
    created = skipped = errors = 0
    for name, module_path in sorted(desired.items()):
        link_path = private_dir / name
        rel_target = pathlib.Path(os.path.relpath(module_path, private_dir))
        if link_path.is_symlink():
            current = (private_dir / os.readlink(link_path)).resolve()
            if current == module_path:
                skipped += 1
                continue
            lp = _rel(link_path)
            print(f"  CONFLICT {lp} already points to {current} — skipping")
            errors += 1
            continue
        if link_path.exists():
            lp = _rel(link_path)
            print(f"  BLOCKED  {lp} exists and is not a symlink — skipping")
            errors += 1
            continue
        if dry_run:
            print(f"  would create  {_rel(link_path)}  →  {rel_target}")
        else:
            link_path.symlink_to(rel_target)
            print(f"  created  {_rel(link_path)}  →  {rel_target}")
        created += 1
    return created, skipped, errors


def _clean_stale(desired, private_dir, dry_run):
    """Remove symlinks in *private_dir* not in *desired*. Returns removed count."""
    removed = 0
    for link_path in sorted(private_dir.iterdir()):
        if not link_path.is_symlink() or link_path.name in desired:
            continue
        if dry_run:
            print(f"  would remove  {_rel(link_path)}  (stale)")
        else:
            link_path.unlink()
            print(f"  removed  {_rel(link_path)}  (stale)")
        removed += 1
    return removed


def cmd_link_modules(args):
    """
    Read all *.txt files in addons_paths/, resolve the listed module paths
    (relative to the project root), and create relative symlinks in
    odoo/custom/src/private/.

    addons_paths/<project>.txt format (paths relative to project root):
        # comment
        odoo/custom/extra-addons/my_project/core/account_ext
        odoo/custom/extra-addons/my_project/sales/crm_custom
    """
    addons_paths_dir = pathlib.Path(PROJECT_DIR) / "addons_paths"
    private_dir = pathlib.Path(PROJECT_DIR) / "odoo" / "custom" / "src" / "private"

    if not addons_paths_dir.is_dir():
        print(f"ERROR: {addons_paths_dir} does not exist.", file=sys.stderr)
        sys.exit(1)
    private_dir.mkdir(parents=True, exist_ok=True)

    desired, err_collect = _collect_desired(addons_paths_dir)
    created, skipped, err_apply = _apply_symlinks(desired, private_dir, args.dry_run)
    removed = _clean_stale(desired, private_dir, args.dry_run) if args.clean else 0

    errors = err_collect + err_apply
    prefix = "[dry-run] " if args.dry_run else ""
    action = "would create" if args.dry_run else "created"
    print(
        f"\n{prefix}{action} {created}, {skipped} already up-to-date"
        + (f", {removed} removed" if args.clean else "")
        + (f", {errors} error(s)" if errors else "")
    )
    if errors:
        sys.exit(1)


# ── argument parser ───────────────────────────────────────────────────────────


def build_parser():
    parser = argparse.ArgumentParser(
        prog="odoo-cli.py",
        description="Manage the Odoo process inside the dev Docker container.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Start Odoo (uses default dev flags)
  python odoo-cli.py start

  # Start against a specific database, installing modules
  python odoo-cli.py start -d myproject -i sale,purchase

  # Update a module after code change
  python odoo-cli.py restart -d myproject -u my_module

  # Check what is running
  python odoo-cli.py status

  # Stream live logs
  python odoo-cli.py logs --follow

  # Install a Python package on the fly
  python odoo-cli.py pip pandas xlrd

  # Open an Odoo shell (interactive REPL)
  python odoo-cli.py shell -d myproject

  # Run an arbitrary command in the container
  python odoo-cli.py exec -- bash -c "pip list | grep odoo"

  # Generate src/private/ symlinks from all extra-addons/*/addons.txt
  python odoo-cli.py link-modules

  # Preview changes without touching the filesystem
  python odoo-cli.py link-modules --dry-run

  # Also remove symlinks whose entries were deleted from addons.txt
  python odoo-cli.py link-modules --clean
""",
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # start
    p = sub.add_parser("start", help="Start Odoo in the background")
    p.add_argument("-d", "--database", metavar="DB", help="Database to connect to")
    p.add_argument(
        "-i", "--install", nargs="+", metavar="MODULE", help="Modules to install (-i)"
    )
    p.add_argument(
        "-u", "--update", nargs="+", metavar="MODULE", help="Modules to update (-u)"
    )
    p.set_defaults(func=cmd_start)

    # stop
    p = sub.add_parser("stop", help="Stop the running Odoo process")
    p.set_defaults(func=cmd_stop)

    # restart
    p = sub.add_parser("restart", help="Stop then start Odoo")
    p.add_argument("-d", "--database", metavar="DB")
    p.add_argument("-i", "--install", nargs="+", metavar="MODULE")
    p.add_argument("-u", "--update", nargs="+", metavar="MODULE")
    p.set_defaults(func=cmd_restart)

    # status
    p = sub.add_parser("status", help="Show container and Odoo process status")
    p.set_defaults(func=cmd_status)

    # logs
    p = sub.add_parser("logs", help="Show Odoo process logs")
    p.add_argument(
        "-n",
        "--lines",
        type=int,
        default=100,
        metavar="N",
        help="Number of lines to show (default: 100)",
    )
    p.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output (blocks until Ctrl+C)",
    )
    p.set_defaults(func=cmd_logs)

    # pip
    p = sub.add_parser("pip", help="pip install packages into the running container")
    p.add_argument("packages", nargs="+", help="Package names (e.g. pandas xlrd)")
    p.set_defaults(func=cmd_pip)

    # shell
    p = sub.add_parser("shell", help="Open an interactive Odoo Python shell")
    p.add_argument("-d", "--database", metavar="DB", help="Database to connect to")
    p.set_defaults(func=cmd_shell)

    # exec
    p = sub.add_parser("exec", help="Run an arbitrary command inside the container")
    p.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command and arguments (use -- to separate from cli flags)",
    )
    p.set_defaults(func=cmd_exec)

    # link-modules
    p = sub.add_parser(
        "link-modules",
        help="Generate symlinks in src/private/ from extra-addons/*/addons.txt",
        description=(
            "Walks odoo/custom/extra-addons/ for addons.txt files, resolves the "
            "listed module paths, and creates relative symlinks in "
            "odoo/custom/src/private/ so doodba can find them."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes",
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Remove symlinks in src/private/ that point into extra-addons/ "
        "but are no longer listed in any addons.txt",
    )
    p.set_defaults(func=cmd_link_modules)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
