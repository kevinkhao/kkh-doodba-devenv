# Odoo 18 — Local Development Environment

This project uses the [Tecnativa Doodba](https://github.com/Tecnativa/doodba)
architecture. Docker Compose orchestrates three containers. Odoo is **not** started
automatically; you start and stop it manually using `odoo-cli.py`.

---

## Stack

| Container            | Image                                           | Purpose                               |
| -------------------- | ----------------------------------------------- | ------------------------------------- |
| `18-0-doodba-odoo-1` | `18-0-doodba-odoo` (built locally)              | Odoo 18 application                   |
| `18-0-doodba-db-1`   | `ghcr.io/tecnativa/postgres-autoconf:18-alpine` | PostgreSQL 18                         |
| `18-0-doodba-smtp-1` | `docker.io/mailhog/mailhog`                     | Fake SMTP (catches all outbound mail) |

**External dependency:** A Traefik v3.2 reverse proxy runs independently on the
`traefik` Docker network. The odoo container is connected to that network and announces
itself with the label
`traefik.http.routers.18-0-doodba-odoo.rule: Host('odoo-18.localhost')`.

---

## First-time setup after cloning

Two gitignored files are required and must be created once after a fresh clone:

```bash
# 1. Symlink docker-compose.yml to the dev environment definition
ln -s devel.yaml docker-compose.yml

# 2. Create the override that keeps the container alive (Odoo is started manually)
cat > docker-compose.override.yml << 'EOF'
services:
  odoo:
    command:
      - sleep
      - infinity
EOF
```

Then proceed with cloning the Odoo source and building the image (see below).

---

## Prerequisites — Odoo source code

`PIP_INSTALL_ODOO` is `false`, meaning the image expects the Odoo source tree at:

```
odoo/custom/src/odoo/       ← Odoo Community source (odoo-bin lives here)
```

The `/usr/local/bin/odoo` binary inside the container is a symlink to
`/opt/odoo/custom/src/odoo/odoo-bin`. **Nothing will work until the source is cloned.**

Clone it once:

```bash
git clone --depth=1 --branch=18.0 https://github.com/odoo/odoo.git odoo/custom/src/odoo
```

Then rebuild the image:

```bash
docker compose build
docker compose up -d       # recreate containers with the new image
```

---

## Starting and stopping the stack

```bash
# Start all containers (db, smtp, odoo — odoo idles with sleep infinity)
docker compose up -d

# Stop all containers (data volumes are preserved)
docker compose down

# Rebuild the image (after Dockerfile or requirements changes)
docker compose build
docker compose up -d
```

---

## Managing the Odoo process — `odoo-cli.py`

The container stays alive indefinitely via `sleep infinity` (defined in
`docker-compose.override.yml`). Use `odoo-cli.py` to start/stop/restart the actual Odoo
server process inside that idle container.

All commands are safe to call from any directory; the script resolves the project root
from its own location.

### Quick reference

```bash
# Start Odoo with default dev flags
python3 odoo-cli.py start

# Start against a specific database
python3 odoo-cli.py start -d myproject

# Start and install modules at first launch
python3 odoo-cli.py start -d myproject -i sale,purchase,my_module

# Link project modules + install requirements, then start Odoo in one shot
# (auto-starts containers if needed — ideal for agents and fresh clones)
python3 odoo-cli.py start -p myproject -d myproject -i account_ext

# Dev loop: restart after a code fix — no -p, no pip, no symlinks (fast)
python3 odoo-cli.py restart -d myproject -u account_ext

# Stop Odoo
python3 odoo-cli.py stop

# Check container and process status
python3 odoo-cli.py status

# Stream live Odoo logs
python3 odoo-cli.py logs --follow

# Show last 50 log lines
python3 odoo-cli.py logs -n 50

# Install a Python package on the fly (ephemeral — lost on container recreate)
python3 odoo-cli.py pip pandas xlrd reportlab

# Open an interactive Odoo Python REPL
python3 odoo-cli.py shell -d myproject

# Run an arbitrary command inside the container
python3 odoo-cli.py exec -- bash -c "pip list | grep odoo"
```

### Default Odoo flags applied on every `start`

```
--workers=0                         single-process mode
--dev=reload,qweb,werkzeug,xml      hot-reload on file change
--limit-memory-soft=0               no memory kill in dev
--limit-time-real=9999999           no timeout
--limit-time-real-cron=9999999      no cron timeout
```

### Logs

Odoo writes to `/opt/odoo/auto/odoo.log` inside the container, which maps to
`./odoo/auto/odoo.log` on the host. Read it directly or via `odoo-cli.py logs`.

---

## Direct shell access

```bash
# Interactive bash session inside the odoo container
docker compose exec odoo bash
```

From inside the shell you can:

- Run `odoo [flags]` directly
- `pip install <package>` for ad-hoc installs
- Inspect the filesystem, test imports, etc.

---

## Accessing services

| Service                   | URL / Address                                                                    |
| ------------------------- | -------------------------------------------------------------------------------- |
| Odoo web UI (via Traefik) | http://odoo-18.localhost                                                         |
| Odoo web UI (direct)      | http://127.0.0.1:18069                                                           |
| Odoo database manager     | http://127.0.0.1:18069/web/database/manager                                      |
| MailHog web UI            | http://127.0.0.1:18025                                                           |
| PostgreSQL                | 127.0.0.1:5432 is not exposed; connect via `docker compose exec db psql -U odoo` |

---

## Database

- **Default DB name** (`PGDATABASE`): `devel` — used when starting Odoo without `-d`
- **PostgreSQL user**: `odoo`
- **PostgreSQL password**: `odoopassword`
- Create a new database through the Odoo DB manager UI or by passing
  `-d newname -i base` on the first `start`

---

## Python dependencies

### On the fly (ephemeral)

```bash
python3 odoo-cli.py pip <package>
# or from inside the container:
pip install <package>
```

Lost when the container is recreated with `docker compose down` + `up`.

### Permanent (baked into the image)

Add packages to `odoo/custom/src/odoo_requirements.txt`, then:

```bash
docker compose build
docker compose up -d
```

---

## Custom modules

Place modules in:

```
odoo/custom/src/private/my_module/
```

That directory is mounted read-only into the container at
`/opt/odoo/custom/src/private/`. After adding a new module, restart Odoo and install it:

```bash
python3 odoo-cli.py restart -d myproject -i my_module
```

Because `--dev=reload` is active, Python file changes inside existing modules are picked
up automatically without a restart. XML/QWeb changes are also hot-reloaded. Changes to
`__manifest__.py` or new Python files require a restart.

---

## Extra-addons — per-project module layout

Modules can be organised in an arbitrary directory tree under
`odoo/custom/extra-addons/`. Each project declares which of its directories are Odoo
modules by adding a `*.txt` file to the top-level `container_configs/` directory. The
`link-modules` command turns those declarations into symlinks inside
`odoo/auto/addons/`, where doodba picks them up alongside the community modules.

### Directory structure

```
container_configs/               ← one .txt file per project
  my_project.txt
  another_project.txt

odoo/custom/extra-addons/
  my_project/
    core/
      account_ext/          ← an Odoo module
    sales/
      crm_custom/           ← an Odoo module
      helpers/              ← shared code, NOT a module — not listed
  another_project/
    modules/
      inventory_ext/
```

### `container_configs/<project>.txt` format

Each file has two optional sections. Comments (`#`) and blank lines are ignored
everywhere. Lines before the first section header are treated as `[addons]`.

```
# container_configs/my_project.txt

[addons]
odoo/custom/extra-addons/my_project/core
odoo/custom/extra-addons/my_project/sales

[requirements]
odoo/custom/extra-addons/my_project/requirements.txt
```

**`[addons]`** — each line is a **container directory** (path relative to project root).
All immediate subdirectories are symlinked into `odoo/auto/addons/` as Odoo modules.
Non-directory entries are skipped automatically.

**`[requirements]`** — each line is a path to a `requirements.txt` (relative to project
root, must live under `odoo/custom/`). Both `workon` and `start -p` / `restart -p` run
`pip install -r` for each listed file inside the container.

One file per project. The filename is free-form (used only for organisation).

### Generating symlinks

```bash
# Preview — no filesystem changes
python3 odoo-cli.py link-modules --dry-run

# Create/update symlinks in odoo/auto/addons/
python3 odoo-cli.py link-modules

# Also remove our symlinks whose entries were deleted from container_configs/
# (leaves doodba-managed community module links untouched)
python3 odoo-cli.py link-modules --clean
```

After running, `odoo/auto/addons/` contains (alongside the community module links):

```
account_ext  →  ../../custom/extra-addons/my_project/core/account_ext
crm_custom   →  ../../custom/extra-addons/my_project/sales/crm_custom
```

`odoo/auto/` is gitignored — the symlinks are runtime state recreated by `link-modules`
or `workon`.

### When to re-run

Re-run `link-modules` whenever you:

- Add an entry to an `addons.txt`
- Create a new `addons.txt` for a new project
- Remove or rename an entry (add `--clean` to remove the stale symlink)

The symlinks live in `odoo/auto/` which is gitignored — run `link-modules` or `workon`
as part of any setup step after cloning.

### `workon` and `start -p` — one-shot project setup

Both commands share the same setup sequence (implemented in `_setup_project`):

1. Read `container_configs/<project>.txt` (error if missing, lists available projects)
2. Collect desired modules and requirement paths (host-side, no side effects)
3. Start the containers (`docker compose up -d`) if they are not already running —
   **containers start before symlinks** so the doodba entrypoint (which initialises
   `auto/addons/` on first container start) does not clobber freshly-created links
4. Create symlinks in `odoo/auto/addons/` for all modules in the `[addons]` section
5. Run `pip install -r` for each path in the `[requirements]` section (if any)

They differ only in the final step:

| Command                                      | Final step                      |
| -------------------------------------------- | ------------------------------- |
| `workon my_project`                          | Opens an interactive bash shell |
| `start -p my_project -d mydb`                | Launches the Odoo process       |
| `restart -p my_project -d mydb -u my_module` | Restarts the Odoo process       |

Use **`workon`** when you want an interactive shell to explore or debug. Use
**`start -p`** / **`restart -p`** when an agent or script needs Odoo running without a
terminal (e.g. CI, automation, first-time setup from a non-interactive context).

### Installing the linked module

After linking, tell Odoo to install it:

```bash
python3 odoo-cli.py restart -d myproject -i account_ext
```

---

## Dev loop

The dev loop is the fast iteration cycle for fixing and testing a module without
repeating the full project setup. It has two phases:

### Phase 1 — setup (once per session or fresh clone)

```bash
python3 odoo-cli.py start -p myproject -d mydb -i my_module
```

This runs `_setup_project`: starts containers, creates symlinks, installs
`requirements.txt`, then launches Odoo with `-i my_module`. Run it once.

### Phase 2 — dev loop (repeated for every code fix)

```bash
# edit source files ...

# then restart — no -p, no pip, no symlinks
python3 odoo-cli.py restart -d mydb -i my_module   # first successful install
python3 odoo-cli.py restart -d mydb -u my_module   # subsequent updates
```

`restart` without `-p` only kills and re-launches the Odoo process. No container touch,
no pip, no symlink work. Typical cycle time is ~10–15 seconds.

Use `-i` (install) when the module has never been successfully installed in the
database. Switch to `-u` (update) once it is installed — `-u` on an uninstalled module
is silently ignored.

### `-i` vs `-u`

| Flag           | When to use                                                          |
| -------------- | -------------------------------------------------------------------- |
| `-i my_module` | First successful install, or after a failed install that rolled back |
| `-u my_module` | Module already installed; apply model/view/data changes              |

### pip packages across restarts

Packages installed via `-p` / `requirements.txt` persist for the lifetime of the
container. Plain `restart` does not re-run pip — the packages are already there. They
are lost only when the container is recreated (`docker compose down` + `up`), at which
point you need to run `start -p` again.

### When to re-run `-p`

| Situation                                  | Command                                       |
| ------------------------------------------ | --------------------------------------------- |
| Fresh clone or after `docker compose down` | `start -p myproject …`                        |
| New entry added to `[requirements]`        | `restart -p myproject …`                      |
| New entry added to `[addons]`              | `restart -p myproject …`                      |
| Code change only                           | `restart -d mydb -u my_module` ← **dev loop** |

---

## Key files

| File                          | Purpose                                                                       |
| ----------------------------- | ----------------------------------------------------------------------------- |
| `devel.yaml`                  | Main compose file for dev (symlinked as `docker-compose.yml`)                 |
| `common.yaml`                 | Base service definitions shared across environments                           |
| `docker-compose.override.yml` | Overrides `command` to `sleep infinity` so Odoo is not auto-started           |
| `odoo-cli.py`                 | CLI for managing the Odoo process, packages, and module links                 |
| `odoo/Dockerfile`             | One-liner: `FROM ghcr.io/tecnativa/doodba:18.0-onbuild`                       |
| `container_configs/`          | One `.txt` file per project; each lists module paths relative to project root |
| `odoo/custom/extra-addons/`   | Per-project module trees (arbitrary structure)                                |
| `odoo/custom/src/private/`    | Flat symlink farm consumed by doodba (links generated by `link-modules`)      |
| `odoo/custom/src/`            | Source tree: `odoo/` (community), `private/` (custom modules)                 |
| `odoo/auto/`                  | Generated config and logs (rw-mounted, gitignored)                            |
| `.env`                        | Sets `COMPOSE_PROJECT_NAME=18-0-doodba` and `PORT_PREFIX=18`                  |
