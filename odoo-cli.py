#!/usr/bin/env python3
"""
Odoo development environment CLI.

Manages the Odoo process inside the running Docker container.
All commands communicate with the 'odoo' service via `docker compose exec`.

Supports multiple simultaneous Odoo instances, each identified by a project
name (the -p flag). The 'default' instance is used when -p is not specified.

Usage:
    python odoo-cli.py <command> [options]

Run `python odoo-cli.py --help` for the full command list.
"""

import argparse
import json
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

# Instance-namespaced files:
#   /tmp/odoo-{instance}.pid      — 3 lines: pid, db, port
#   /opt/odoo/auto/odoo-{instance}.log
# The legacy /tmp/odoo.pid (single PID) is read as a migration shim for the
# 'default' instance only.
LEGACY_PID_FILE = "/tmp/odoo.pid"
LOG_DIR = "/opt/odoo/auto"

# Volume mount: PROJECT_DIR/odoo/custom → /opt/odoo/custom inside the container
HOST_CUSTOM = pathlib.Path(PROJECT_DIR) / "odoo" / "custom"
CONTAINER_CUSTOM = "/opt/odoo/custom"

# Where symlinks are placed so doodba picks up custom modules
SYMLINK_DIR = pathlib.Path(PROJECT_DIR) / "odoo" / "auto" / "addons"
EXTRA_ADDONS_DIR = pathlib.Path(PROJECT_DIR) / "odoo" / "custom" / "extra-addons"

DEFAULT_PORT = 8069
PORT_POOL = range(8070, 8100)  # 30 slots for named instances

DEFAULT_FLAGS = [
    "--workers=0",
    "--dev=reload,qweb,werkzeug,xml",
    "--limit-memory-soft=0",
    "--limit-time-real=9999999",
    "--limit-time-real-cron=9999999",
]

# Traefik file-provider: odoo-cli.py writes one YAML per running instance so
# Traefik can route each port dynamically without touching compose labels.
# Base hostname for the default instance; named instances get {name}.{base}.
TRAEFIK_DYNAMIC_DIR = pathlib.Path.home() / ".traefik" / "dynamic"
TRAEFIK_BASE_HOSTNAME = "odoo-18.localhost"

# ── project-setup workflow ────────────────────────────────────────────────────
#
# _setup_project(project) is called by `start -p` / `restart -p` and `workon`.
#
# Steps executed in order:
#   1. Resolve container_configs/<project>.txt
#      Exits with an error (and lists available projects) if the file is missing.
#   2. Collect (host-side only, no side effects)
#      _collect_desired + _collect_requirements — reads config, resolves paths.
#   3. Start containers if not running
#      Runs `docker compose up -d` BEFORE creating symlinks: the doodba entrypoint
#      initialises auto/addons/ on container start and would clobber any symlinks
#      written before it runs.
#   4. Link modules
#      _apply_symlinks → creates relative symlinks in odoo/auto/addons/ for every
#      immediate subdirectory of each [addons] entry.
#   5. Install requirements
#      `pip install -r` for each path listed in [requirements]; no-op when absent.
#
# Callers:
#   start / restart  with -p/--project  →  setup then launch the Odoo process
#   workon           always             →  setup then open an interactive bash shell

# ── helpers ───────────────────────────────────────────────────────────────────


def _exec(cmd, *, interactive=False, check=True, capture=False, **kwargs):
    """Run *cmd* inside the odoo container via docker compose exec."""
    tty_flags = ["-it"] if interactive else ["-T"]
    full = COMPOSE + ["exec"] + tty_flags + [SERVICE] + cmd
    if capture:
        return subprocess.run(
            full, check=check, text=True, capture_output=True, **kwargs
        )
    return subprocess.run(full, check=check, **kwargs)


def _container_running():
    """Return True if the odoo container itself is up."""
    r = subprocess.run(
        COMPOSE + ["ps", "--status", "running", "--services"],
        check=False,
        text=True,
        capture_output=True,
    )
    return SERVICE in r.stdout.splitlines()


# ── instance helpers ──────────────────────────────────────────────────────────


def _instance(args):
    """Return the instance name from args, defaulting to 'default'."""
    return getattr(args, "project", None) or "default"


def _pid_file(instance):
    return f"/tmp/odoo-{instance}.pid"


def _log_file(instance):
    return f"{LOG_DIR}/odoo-{instance}.log"


def _host_port(container_port):
    """Convert a container port to its host-mapped port using PORT_PREFIX from .env."""
    prefix = "18"
    env_file = pathlib.Path(PROJECT_DIR) / ".env"
    try:
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("PORT_PREFIX="):
                prefix = line.split("=", 1)[1].strip()
                break
    except FileNotFoundError:
        pass
    # 8069 → 18069, 8070 → 18070, etc.
    return f"{prefix}{str(container_port)[1:]}"


def _is_running(instance):
    """Return True if a live Odoo process exists for *instance* inside the container."""
    pid_file = _pid_file(instance)
    r = _exec(
        [
            "bash",
            "-c",
            f"[ -f {pid_file} ] && kill -0 \"$(sed -n '1p' {pid_file})\" 2>/dev/null",
        ],
        check=False,
    )
    if r.returncode == 0:
        return True
    # Migration shim: check legacy /tmp/odoo.pid for the 'default' instance
    if instance == "default":
        lf = LEGACY_PID_FILE
        r2 = _exec(
            ["bash", "-c", f'[ -f {lf} ] && kill -0 "$(cat {lf})" 2>/dev/null'],
            check=False,
        )
        return r2.returncode == 0
    return False


def _read_pid_meta(instance):
    """Return (pid, db, port) from the PID file for *instance*, or (None, None, None).

    PID file format — 3 lines: pid, db, port.
    Falls back to the legacy /tmp/odoo.pid for the 'default' instance if the
    new-style file does not exist (migration shim).
    """
    pid_file = _pid_file(instance)
    r = _exec(["bash", "-c", f"cat {pid_file} 2>/dev/null"], capture=True, check=False)

    if not r.stdout.strip() and instance == "default":
        r = _exec(
            ["bash", "-c", f"cat {LEGACY_PID_FILE} 2>/dev/null"],
            capture=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            print(
                "Note: reading legacy /tmp/odoo.pid — will migrate on next restart.",
                file=sys.stderr,
            )
            try:
                return int(r.stdout.strip().splitlines()[0]), None, DEFAULT_PORT
            except (ValueError, IndexError):
                return None, None, None
        return None, None, None

    if not r.stdout.strip():
        return None, None, None

    lines = r.stdout.strip().splitlines()
    try:
        pid = int(lines[0])
    except (ValueError, IndexError):
        return None, None, None

    db = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None
    port_str = lines[2].strip() if len(lines) > 2 else ""
    port = int(port_str) if port_str.isdigit() else None
    return pid, db, port


def _list_instances():
    """Return all live Odoo instances as [{instance, pid, db, port}]."""
    script = (
        "for f in /tmp/odoo-*.pid; do\n"
        '    [ -f "$f" ] || continue\n'
        "    pid=$(sed -n '1p' \"$f\")\n"
        '    kill -0 "$pid" 2>/dev/null || continue\n'
        '    name=$(basename "$f" .pid); name=${name#odoo-}\n'
        "    db=$(sed -n '2p' \"$f\")\n"
        "    port=$(sed -n '3p' \"$f\")\n"
        '    echo "$name|$pid|$db|$port"\n'
        "done\n"
    )
    r = _exec(["bash", "-c", script], capture=True, check=False)
    instances = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        db = parts[2] if len(parts) > 2 and parts[2] else None
        port_str = parts[3] if len(parts) > 3 else ""
        port = int(port_str) if port_str.isdigit() else None
        instances.append({"instance": parts[0], "pid": pid, "db": db, "port": port})

    # Migration shim: pick up legacy /tmp/odoo.pid if 'default' is not found
    if not any(i["instance"] == "default" for i in instances):
        r2 = _exec(
            ["bash", "-c", f"[ -f {LEGACY_PID_FILE} ] && cat {LEGACY_PID_FILE}"],
            capture=True,
            check=False,
        )
        if r2.returncode == 0 and r2.stdout.strip():
            try:
                pid = int(r2.stdout.strip().splitlines()[0])
                r3 = _exec(["bash", "-c", f"kill -0 {pid} 2>/dev/null"], check=False)
                if r3.returncode == 0:
                    instances.append(
                        {
                            "instance": "default",
                            "pid": pid,
                            "db": None,
                            "port": DEFAULT_PORT,
                        }
                    )
            except (ValueError, IndexError):
                pass

    return instances


def _auto_detect_instance():
    """Return the instance name when exactly one is running.

    Print error and return None when zero or multiple instances are running.
    """
    instances = _list_instances()
    if len(instances) == 0:
        print("ERROR: No Odoo instance is running. Use 'start' first.", file=sys.stderr)
        return None
    if len(instances) == 1:
        return instances[0]["instance"]
    print("ERROR: Multiple instances running — specify -p PROJECT:", file=sys.stderr)
    for i in instances:
        detail = f"  {i['instance']:<16} PID {i['pid']}"
        if i["db"]:
            detail += f", DB {i['db']}"
        if i["port"]:
            detail += f", port {i['port']}"
        print(detail, file=sys.stderr)
    return None


def _assign_port(instance):
    """Return a port for *instance*.

    DEFAULT_PORT for 'default', else the lowest free port in PORT_POOL.
    """
    if instance == "default":
        return DEFAULT_PORT
    used = {i["port"] for i in _list_instances() if i["port"]}
    for port in PORT_POOL:
        if port not in used:
            return port
    print(
        f"WARNING: port pool {PORT_POOL.start}–{PORT_POOL.stop - 1} exhausted,"
        f" reusing {PORT_POOL.start}.",
        file=sys.stderr,
    )
    return PORT_POOL.start


def _kill_instance(instance):
    """Kill the Odoo process for *instance* and remove its PID file."""
    _remove_traefik_route(instance)
    pid_file = _pid_file(instance)
    r = _exec(["bash", "-c", f"[ -f {pid_file} ]"], check=False)
    if r.returncode == 0:
        _exec(["bash", "-c", f"kill \"$(sed -n '1p' {pid_file})\" && rm -f {pid_file}"])
        return
    # Migration shim
    if instance == "default":
        r2 = _exec(["bash", "-c", f"[ -f {LEGACY_PID_FILE} ]"], check=False)
        if r2.returncode == 0:
            _exec(
                [
                    "bash",
                    "-c",
                    f'kill "$(cat {LEGACY_PID_FILE})" && rm -f {LEGACY_PID_FILE}',
                ]
            )


# ── project-setup helpers ─────────────────────────────────────────────────────


def _setup_project(project):
    """Link modules and install requirements for *project*."""
    addons_paths_dir = pathlib.Path(PROJECT_DIR) / "container_configs"
    addons_file = addons_paths_dir / f"{project}.txt"
    if not addons_file.exists():
        print(f"ERROR: container_configs/{project}.txt not found.", file=sys.stderr)
        available = sorted(
            f.stem
            for f in addons_paths_dir.iterdir()
            if f.suffix == ".txt" and not f.name.startswith(".")
        )
        if available:
            print(f"Available projects: {', '.join(available)}", file=sys.stderr)
        sys.exit(1)

    desired, err_collect = _collect_desired([addons_file])
    req_paths, err_req = _collect_requirements([addons_file])
    setup_paths, err_setup = _collect_setup([addons_file])
    if err_collect or err_req or err_setup:
        sys.exit(1)

    if not _container_running():
        print("Starting containers...")
        subprocess.run(COMPOSE + ["up", "-d"], check=True)

    print(f"Linking modules for '{project}'...")
    SYMLINK_DIR.mkdir(parents=True, exist_ok=True)
    created, skipped, err_apply = _apply_symlinks(desired, SYMLINK_DIR, dry_run=False)
    print(
        f"  {created} linked, {skipped} already up-to-date"
        + (f", {err_apply} error(s)" if err_apply else "")
    )
    if err_apply:
        sys.exit(1)

    if setup_paths:
        print("Running setup scripts...")
        for script in setup_paths:
            print(f"  bash {_rel(script)}")
            subprocess.run(
                COMPOSE + ["exec", "-T", "--user", "root", SERVICE, "bash"],
                input=script.read_text(),
                text=True,
                check=True,
            )

    if req_paths:
        print("Installing requirements...")
        for req in req_paths:
            print(f"  pip install -r {req}")
            _exec(["pip", "install", "--no-cache-dir", "-r", req])


# ── traefik routing ───────────────────────────────────────────────────────────


def _compose_project():
    """Return COMPOSE_PROJECT_NAME from .env (fallback: directory name)."""
    env_file = pathlib.Path(PROJECT_DIR) / ".env"
    try:
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("COMPOSE_PROJECT_NAME="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return pathlib.Path(PROJECT_DIR).name.lower()


def _traefik_hostname(instance):
    """Return the Traefik Host() hostname for this instance.

    default → 'odoo-18.localhost'
    samotics → 'samotics.odoo-18.localhost'
    """
    if instance == "default":
        return TRAEFIK_BASE_HOSTNAME
    return f"{instance}.{TRAEFIK_BASE_HOSTNAME}"


def _write_traefik_route(instance, port):
    """Write (or overwrite) the Traefik file-provider YAML for this instance.

    Traefik watches TRAEFIK_DYNAMIC_DIR and hot-reloads within ~100ms.
    Returns the hostname so callers can print access instructions.
    """
    TRAEFIK_DYNAMIC_DIR.mkdir(parents=True, exist_ok=True)
    hostname = _traefik_hostname(instance)
    project = _compose_project()
    container = f"{project}-odoo-1"
    router = f"{project}-{instance}"
    route_file = TRAEFIK_DYNAMIC_DIR / f"{router}.yml"
    route_file.write_text(
        f"# Auto-generated by odoo-cli.py — instance: {instance}  port: {port}\n"
        f"http:\n"
        f"  routers:\n"
        f"    {router}:\n"
        f"      rule: 'Host(`{hostname}`)'\n"
        f"      service: {router}\n"
        f"      entryPoints:\n"
        f"        - http\n"
        f"  services:\n"
        f"    {router}:\n"
        f"      loadBalancer:\n"
        f"        servers:\n"
        f'          - url: "http://{container}:{port}"\n'
    )
    return hostname


def _remove_traefik_route(instance):
    """Delete the Traefik file-provider YAML for this instance if it exists."""
    project = _compose_project()
    route_file = TRAEFIK_DYNAMIC_DIR / f"{project}-{instance}.yml"
    try:
        route_file.unlink()
    except FileNotFoundError:
        pass


def _check_hosts_entry(hostname):
    """Print a sudo command to add the hostname if it is absent from /etc/hosts."""
    try:
        if hostname not in pathlib.Path("/etc/hosts").read_text():
            print(
                f"\n  Add to /etc/hosts for browser access:\n"
                f"    echo '127.0.0.1 {hostname}' | sudo tee -a /etc/hosts"
            )
    except OSError:
        pass


# ── commands ──────────────────────────────────────────────────────────────────


def cmd_start(args):
    if getattr(args, "project", None) and not getattr(args, "_no_setup", False):
        _setup_project(args.project)

    if not _container_running():
        print(
            "ERROR: The odoo container is not running. Run: docker compose up -d",
            file=sys.stderr,
        )
        sys.exit(1)

    instance = _instance(args)

    if _is_running(instance):
        print(
            f"Odoo instance '{instance}' is already running."
            " Use 'restart' to restart it."
        )
        sys.exit(0)

    db = args.database
    port = getattr(args, "port", None) or _assign_port(instance)
    log_file = _log_file(instance)
    pid_file = _pid_file(instance)

    flags = list(DEFAULT_FLAGS) + [f"--xmlrpc-port={port}"]
    if db:
        flags += ["-d", db]
    if getattr(args, "install", None):
        flags += ["-i", ",".join(args.install)]
    if getattr(args, "update", None):
        flags += ["-u", ",".join(args.update)]

    odoo_cmd = "odoo " + " ".join(flags)
    launch = (
        f"nohup {odoo_cmd} > {log_file} 2>&1 & "
        f'_PID=$! && '
        f'printf "%s\\n%s\\n%s\\n" "$_PID" "{db or ""}" "{port}" > {pid_file} && '
        f'echo "Odoo instance \'{instance}\' started (PID $_PID, port {port}). '
        f'Logs: {log_file}"'
    )
    _exec(["bash", "-c", launch])
    hostname = _write_traefik_route(instance, port)
    _check_hosts_entry(hostname)


def cmd_stop(args):
    instance = getattr(args, "project", None)
    if not instance:
        instance = _auto_detect_instance()
        if not instance:
            sys.exit(1)

    if not _is_running(instance):
        print(f"Odoo instance '{instance}' is not running.")
        return

    _kill_instance(instance)
    print(f"Odoo instance '{instance}' stopped.")


def cmd_restart(args):
    explicit_project = getattr(args, "project", None)
    instance = explicit_project

    if not explicit_project:
        instances = _list_instances()
        if len(instances) == 0:
            # Nothing running — start fresh as 'default'
            instance = "default"
            args.project = "default"
        elif len(instances) == 1:
            instance = instances[0]["instance"]
            args.project = instance
        else:
            print(
                "ERROR: Multiple instances running — specify -p PROJECT:",
                file=sys.stderr,
            )
            for i in instances:
                detail = f"  {i['instance']:<16} PID {i['pid']}"
                if i["db"]:
                    detail += f", DB {i['db']}"
                if i["port"]:
                    detail += f", port {i['port']}"
                print(detail, file=sys.stderr)
            sys.exit(1)
    else:
        args.project = instance

    # Infer -d and --port from stored metadata when not explicitly provided
    _, stored_db, stored_port = _read_pid_meta(instance)
    if not args.database and stored_db:
        args.database = stored_db
        print(f"Using database '{stored_db}' from instance metadata.")
    if not getattr(args, "port", None) and stored_port:
        args.port = stored_port

    if _is_running(instance):
        _kill_instance(instance)
        print("Stopped. Waiting for process to exit...")
        time.sleep(2)

    if not explicit_project:
        args._no_setup = True
    cmd_start(args)


def cmd_status(args):
    if not _container_running():
        print("Container: not running")
        return
    print("Container: running")

    instance = getattr(args, "project", None)
    use_json = getattr(args, "json", False)

    if instance:
        pid, db, port = _read_pid_meta(instance)
        running = _is_running(instance)
        if use_json:
            print(
                json.dumps(
                    {
                        "instance": instance,
                        "pid": pid,
                        "db": db,
                        "port": port,
                        "running": running,
                    }
                )
            )
            return
        if running:
            print(f"Instance:  {instance}")
            print(f"Odoo:      running (PID {pid})")
            if db:
                print(f"Database:  {db}")
            if port:
                print(f"Port:      {port}")
                print(f"URL:       http://{_traefik_hostname(instance)}")
                print(f"Direct:    http://127.0.0.1:{_host_port(port)}")
            print(f"Logs:      {_log_file(instance)}")
            print(f"           host: ./odoo/auto/odoo-{instance}.log")
        else:
            print(f"Instance:  {instance}")
            print("Odoo:      not running")
        return

    # All instances
    instances = _list_instances()
    if use_json:
        print(json.dumps(instances))
        return
    if not instances:
        print("Odoo:      not running")
        return

    col_i = max(len("INSTANCE"), max(len(i["instance"]) for i in instances))
    col_p = max(len("PID"), max(len(str(i["pid"])) for i in instances))
    col_d = max(len("DATABASE"), max(len(i["db"] or "—") for i in instances))
    col_o = len("PORT")
    fmt = f"{{:<{col_i}}}  {{:<{col_p}}}  {{:<{col_d}}}  {{:<{col_o}}}"
    print(fmt.format("INSTANCE", "PID", "DATABASE", "PORT"))
    print(fmt.format("─" * col_i, "─" * col_p, "─" * col_d, "─" * col_o))
    for i in instances:
        print(
            fmt.format(
                i["instance"],
                str(i["pid"]),
                i["db"] or "—",
                str(i["port"]) if i["port"] else "—",
            )
        )


def cmd_logs(args):
    if not _container_running():
        print("ERROR: Container is not running.", file=sys.stderr)
        sys.exit(1)

    instance = getattr(args, "project", None)
    if not instance:
        instance = _auto_detect_instance()
        if not instance:
            sys.exit(1)

    log_file = _log_file(instance)
    n = args.lines if args.lines else 100
    flags = f"-n {n}" + (" -f" if args.follow else "")
    no_log_msg = f"No log file yet — start instance '{instance}' first."
    bash = f"tail {flags} {log_file} 2>/dev/null || echo '{no_log_msg}'"
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
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        print("ERROR: Provide a command to run.", file=sys.stderr)
        sys.exit(1)
    _exec(cmd, interactive=sys.stdin.isatty())


def _rel(path):
    """Return *path* relative to PROJECT_DIR for display."""
    return pathlib.Path(path).relative_to(PROJECT_DIR)


def _parse_config(config_file):
    """Parse a container config .txt file into {section: [lines]}.

    Section headers are [addons] and [requirements]. Lines before the first
    header are treated as belonging to [addons] for backward compatibility.
    Comments and blank lines are ignored everywhere.
    """
    sections: dict[str, list[str]] = {"addons": [], "requirements": []}
    current = "addons"
    for raw in config_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].lower()
            if current not in sections:
                sections[current] = []
        else:
            sections[current].append(line)
    return sections


def _collect_desired(config_files):
    """Return ({name: module_path}, error_count) from an iterable of .txt files."""
    desired: dict[str, pathlib.Path] = {}
    errors = 0
    for addons_file in config_files:
        src = _rel(addons_file)
        for line in _parse_config(addons_file)["addons"]:
            container_path = (pathlib.Path(PROJECT_DIR) / line).resolve()
            if not container_path.exists():
                print(f"  MISSING  {container_path}  (from {src})")
                errors += 1
                continue
            if not container_path.is_dir():
                print(f"  NOT_DIR  {container_path}  (from {src})")
                errors += 1
                continue
            for module_path in sorted(container_path.iterdir()):
                if not module_path.is_dir() or module_path.name.startswith("."):
                    continue
                name = module_path.name
                desired[name] = module_path
    return desired, errors


def _collect_requirements(config_files):
    """Return ([container_paths], error_count) for [requirements] entries."""
    req_paths: list[str] = []
    errors = 0
    for addons_file in config_files:
        src = _rel(addons_file)
        for line in _parse_config(addons_file)["requirements"]:
            host_path = (pathlib.Path(PROJECT_DIR) / line).resolve()
            if not host_path.exists():
                print(f"  MISSING  {host_path}  (from {src})")
                errors += 1
                continue
            try:
                rel = host_path.relative_to(HOST_CUSTOM)
            except ValueError:
                print(
                    f"  OUT_OF_MOUNT  {host_path} is not under"
                    f" odoo/custom/  (from {src})"
                )
                errors += 1
                continue
            req_paths.append(f"{CONTAINER_CUSTOM}/{rel}")
    return req_paths, errors


def _collect_setup(config_files):
    """Return ([host_path], error_count) for [setup] script entries.

    Scripts are piped to bash via stdin so they don't need to be inside the
    bind-mounted volume — they can live anywhere on the host (e.g. container_configs/).
    """
    script_paths: list[pathlib.Path] = []
    errors = 0
    for addons_file in config_files:
        src = _rel(addons_file)
        for line in _parse_config(addons_file).get("setup", []):
            host_path = (pathlib.Path(PROJECT_DIR) / line).resolve()
            if not host_path.exists():
                print(f"  MISSING  {host_path}  (from {src})")
                errors += 1
                continue
            script_paths.append(host_path)
    return script_paths, errors


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
            if dry_run:
                print(f"  would relink  {_rel(link_path)}  →  {rel_target}")
            else:
                link_path.unlink()
                link_path.symlink_to(rel_target)
                print(f"  relinked  {_rel(link_path)}  →  {rel_target}")
            created += 1
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


def _clean_stale(desired, symlink_dir, dry_run):
    """Remove our symlinks in *symlink_dir* not in *desired*.

    Only touches symlinks whose resolved target is under EXTRA_ADDONS_DIR,
    leaving doodba-managed links (e.g. community module links) untouched.
    Returns removed count.
    """
    removed = 0
    for link_path in sorted(symlink_dir.iterdir()):
        if not link_path.is_symlink() or link_path.name in desired:
            continue
        target = (symlink_dir / os.readlink(link_path)).resolve()
        if not str(target).startswith(str(EXTRA_ADDONS_DIR)):
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
    Read all *.txt files in container_configs/, expand each listed directory into
    its immediate subdirectories, and create relative symlinks in
    odoo/auto/addons/ so doodba picks them up alongside the community modules.
    """
    addons_paths_dir = pathlib.Path(PROJECT_DIR) / "container_configs"

    if not addons_paths_dir.is_dir():
        print(f"ERROR: {addons_paths_dir} does not exist.", file=sys.stderr)
        sys.exit(1)
    SYMLINK_DIR.mkdir(parents=True, exist_ok=True)

    config_files = sorted(
        f
        for f in addons_paths_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
    )
    desired, err_collect = _collect_desired(config_files)
    created, skipped, err_apply = _apply_symlinks(desired, SYMLINK_DIR, args.dry_run)
    removed = _clean_stale(desired, SYMLINK_DIR, args.dry_run) if args.clean else 0

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


def cmd_workon(args):
    """Link a project's modules and open an interactive shell in the container."""
    _setup_project(args.project)
    _exec(["bash"], interactive=True)


# ── argument parser ───────────────────────────────────────────────────────────


def build_parser():
    parser = argparse.ArgumentParser(
        prog="odoo-cli.py",
        description="Manage the Odoo process inside the dev Docker container.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
examples:
  # Start Odoo (default instance, port {DEFAULT_PORT})
  python odoo-cli.py start

  # Start against a specific database
  python odoo-cli.py start -d myproject

  # Full project setup: link modules + install requirements + start Odoo
  python odoo-cli.py start -p myproject -d myproject -i account_ext

  # Dev loop: restart after a code fix (DB and port inferred from PID file)
  python odoo-cli.py restart -u account_ext

  # Restart a specific instance
  python odoo-cli.py restart -p samotics -u my_module

  # Check all running instances
  python odoo-cli.py status

  # Check a specific instance (machine-readable)
  python odoo-cli.py status -p samotics --json

  # Stop a specific instance
  python odoo-cli.py stop -p samotics

  # Stream live logs (auto-detects instance when only one is running)
  python odoo-cli.py logs --follow

  # Stream logs for a specific instance
  python odoo-cli.py logs -p fullavl --follow

  # Install a Python package on the fly
  python odoo-cli.py pip pandas xlrd

  # Open an Odoo shell (interactive REPL)
  python odoo-cli.py shell -d myproject

  # Run an arbitrary command in the container
  python odoo-cli.py exec -- bash -c "pip list | grep odoo"

  # Link a project's modules and open a shell
  python odoo-cli.py workon my_project

  # Generate odoo/auto/addons/ symlinks from all container_configs/*.txt
  python odoo-cli.py link-modules

  # Preview changes without touching the filesystem
  python odoo-cli.py link-modules --dry-run

  # Also remove symlinks whose entries were deleted from container_configs/
  python odoo-cli.py link-modules --clean
""",
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # start
    p = sub.add_parser("start", help="Start Odoo in the background")
    p.add_argument("-d", "--database", metavar="DB", help="Database to connect to")
    p.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help="Instance name + project setup: link modules and install requirements "
        "from container_configs/PROJECT.txt before starting "
        "(auto-starts containers if needed)",
    )
    p.add_argument(
        "-P",
        "--port",
        metavar="PORT",
        type=int,
        help=(
            f"xmlrpc port inside the container "
            f"(default: {DEFAULT_PORT} for 'default' instance, "
            f"auto-assigned from {PORT_POOL.start}–{PORT_POOL.stop - 1}"
            f" for named instances)"
        ),
    )
    p.add_argument(
        "-i", "--install", nargs="+", metavar="MODULE", help="Modules to install (-i)"
    )
    p.add_argument(
        "-u", "--update", nargs="+", metavar="MODULE", help="Modules to update (-u)"
    )
    p.set_defaults(func=cmd_start)

    # stop
    p = sub.add_parser("stop", help="Stop a running Odoo instance")
    p.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help="Instance to stop (auto-detected when only one is running)",
    )
    p.set_defaults(func=cmd_stop)

    # restart
    p = sub.add_parser("restart", help="Stop then start Odoo")
    p.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help="Instance to restart (auto-detected when only one is running)",
    )
    p.add_argument(
        "-d",
        "--database",
        metavar="DB",
        help="Database to connect to (inferred from PID file if omitted)",
    )
    p.add_argument(
        "-P",
        "--port",
        metavar="PORT",
        type=int,
        help="xmlrpc port (inferred from PID file if omitted)",
    )
    p.add_argument(
        "-i", "--install", nargs="+", metavar="MODULE", help="Modules to install (-i)"
    )
    p.add_argument(
        "-u", "--update", nargs="+", metavar="MODULE", help="Modules to update (-u)"
    )
    p.set_defaults(func=cmd_restart)

    # status
    p = sub.add_parser("status", help="Show container and Odoo process status")
    p.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help=(
            "Show status for a specific instance (default: show all running instances)"
        ),
    )
    p.add_argument(
        "--json", action="store_true", help="Output as JSON (agent-friendly)"
    )
    p.set_defaults(func=cmd_status)

    # logs
    p = sub.add_parser("logs", help="Show Odoo process logs")
    p.add_argument(
        "-p",
        "--project",
        metavar="PROJECT",
        help="Instance to show logs for (auto-detected when only one is running)",
    )
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

    # workon
    p = sub.add_parser(
        "workon",
        help="Link a project's modules and open a shell in the container",
        description=(
            "Reads container_configs/PROJECT.txt, creates symlinks in"
            " odoo/auto/addons/, starts the containers if needed,"
            " then opens an interactive bash shell."
        ),
    )
    p.add_argument(
        "project",
        metavar="PROJECT",
        help="Project name (reads container_configs/PROJECT.txt)",
    )
    p.set_defaults(func=cmd_workon)

    # link-modules
    p = sub.add_parser(
        "link-modules",
        help="Generate symlinks in odoo/auto/addons/ from container_configs/*.txt",
        description=(
            "Reads *.txt files in container_configs/, expands each listed directory "
            "into its immediate subdirectories, and creates relative symlinks in "
            "odoo/auto/addons/ so doodba can find them."
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
        help="Remove symlinks in odoo/auto/addons/ that point into extra-addons/ "
        "but are no longer listed in any container_configs/*.txt",
    )
    p.set_defaults(func=cmd_link_modules)

    return parser


def main():
    sys.stdout.reconfigure(line_buffering=True)
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
