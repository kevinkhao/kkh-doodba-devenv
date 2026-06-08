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

# Restart and update a module after code changes
python3 odoo-cli.py restart -d myproject -u my_module

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
`link-modules` command turns those declarations into symlinks inside `src/private/`,
where doodba picks them up automatically.

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
All immediate subdirectories are symlinked into `src/private/` as Odoo modules.
Non-directory entries are skipped automatically.

**`[requirements]`** — each line is a path to a `requirements.txt` (relative to project
root, must live under `odoo/custom/`). The `workon` command runs `pip install -r` for
each listed file inside the container.

One file per project. The filename is free-form (used only for organisation).

### Generating symlinks

```bash
# Preview — no filesystem changes
python3 odoo-cli.py link-modules --dry-run

# Create/update symlinks in odoo/custom/src/private/
python3 odoo-cli.py link-modules

# Also remove symlinks whose entries were deleted from addons.txt
python3 odoo-cli.py link-modules --clean
```

After running, `src/private/` contains:

```
account_ext  →  ../../extra-addons/my_project/core/account_ext
crm_custom   →  ../../extra-addons/my_project/sales/crm_custom
```

The relative targets resolve identically on the host and inside the container because
both directories live under the same volume mount (`./odoo/custom`).

### When to re-run

Re-run `link-modules` whenever you:

- Add an entry to an `addons.txt`
- Create a new `addons.txt` for a new project
- Remove or rename an entry (add `--clean` to remove the stale symlink)

The generated symlinks can be committed to git — they are small, and committing them
makes the project self-contained for other developers. Alternatively, add
`odoo/custom/src/private/*` to `.gitignore` and run `link-modules` as part of any setup
step.

### `workon` — one-shot project setup

`workon` combines linking and shell access in a single step:

```bash
python3 odoo-cli.py workon my_project
```

It:

1. Reads `container_configs/my_project.txt` (errors if missing, lists available
   projects)
2. Creates symlinks in `src/private/` for all modules in the `[addons]` section
3. Starts the containers (`docker compose up -d`) if they are not already running
4. Runs `pip install -r` for each path in the `[requirements]` section (if any)
5. Opens an interactive bash shell inside the odoo container

This is the fastest way to start working on a project after a fresh clone or after
switching between projects.

### Installing the linked module

After linking, tell Odoo to install it:

```bash
python3 odoo-cli.py restart -d myproject -i account_ext
```

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
