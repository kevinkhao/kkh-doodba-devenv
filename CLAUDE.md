# Odoo 17 — Local Development Environment

[Tecnativa Doodba](https://github.com/Tecnativa/doodba) architecture. Three Docker
containers. Odoo is **not** auto-started — managed manually with `odoo-cli.py`. For full
CLI reference use `/odoo-cli`.

One container hosts several Odoo instances at once (one per `-p PROJECT`). Each instance
gets its own **instance environment** in `odoo/auto/instances/<project>/`: an `addons/`
dir with symlinks to exactly the modules in `container_configs/<project>.txt`, and an
`odoo.conf` whose `addons_path` is that dir plus community addons. Instances never see
each other's modules, so parallel agents can work on different projects safely. The
environment is built by `start -p` / `restart -p` / `workon` and removed by `stop`.

## Stack

| Container            | Image                                           | Purpose                               |
| -------------------- | ----------------------------------------------- | ------------------------------------- |
| `17-0-doodba-odoo-1` | `17-0-doodba-odoo` (built locally)              | Odoo 17 application                   |
| `17-0-doodba-db-1`   | `ghcr.io/tecnativa/postgres-autoconf:16-alpine` | PostgreSQL 16                         |
| `17-0-doodba-smtp-1` | `docker.io/mailhog/mailhog`                     | Fake SMTP (catches all outbound mail) |

Traefik v3.2 reverse proxy runs independently on the `traefik` Docker network. Routing
is managed by `odoo-cli.py` via the Traefik file provider: each `start` writes a YAML
file to `~/.traefik/dynamic/` that maps `{instance}.odoo-17.localhost` to the container
port; each `stop` removes it. Docker labels on the container are disabled
(`traefik.enable: "false"`).

## First-time setup after cloning

```bash
# 1. Symlink docker-compose.yml to the dev environment definition
ln -s devel.yaml docker-compose.yml

# 2. Keep container alive (Odoo is started manually)
cat > docker-compose.override.yml << 'EOF'
services:
  odoo:
    command:
      - sleep
      - infinity
EOF

# 3. Clone Odoo source (required — image does not bundle it) and Enterprise
#    (needs SSH access to github.com/odoo/enterprise; list it in container_configs)
git clone --depth=1 --branch=17.0 https://github.com/odoo/odoo.git odoo/custom/src/odoo
git clone --depth=1 --branch=17.0 git@github.com:odoo/enterprise.git \
  odoo/extra-addons/enterprise

# 4. Build and start
docker compose build
docker compose up -d
```

## Stack commands

```bash
docker compose up -d       # start all containers
docker compose down        # stop (volumes preserved)
docker compose build       # rebuild image after Dockerfile / requirements changes
```

## Accessing services

| Service                    | URL                                                  |
| -------------------------- | ---------------------------------------------------- |
| Odoo default (via Traefik) | http://odoo-17.localhost                             |
| Odoo named instance        | http://{instance}.odoo-17.localhost † (Traefik only) |
| Odoo default (direct)      | http://127.0.0.1:17069                               |
| DB manager                 | http://127.0.0.1:17069/web/database/manager          |
| MailHog                    | http://127.0.0.1:17025                               |
| PostgreSQL                 | `docker compose exec db psql -U odoo`                |

† `start` / `workon` write the Traefik route and print the URL. `*.localhost` resolves
to 127.0.0.1 via systemd-resolved; otherwise the CLI prints the `/etc/hosts` line.

## Database

- Default DB: `devel` (used when no `-d` flag)
- User: `odoo` / Password: `odoopassword`

## Key files

| File                          | Purpose                                                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `devel.yaml`                  | Main compose file for dev (symlinked as `docker-compose.yml`)                                                             |
| `common.yaml`                 | Base service definitions shared across environments                                                                       |
| `docker-compose.override.yml` | Overrides `command` to `sleep infinity` so Odoo is not auto-started                                                       |
| `odoo-cli.py`                 | CLI for instances: environments, start/stop, logs, pip, tests                                                             |
| `odoo/Dockerfile`             | One-liner: `FROM ghcr.io/tecnativa/doodba:17.0-onbuild`                                                                   |
| `container_configs/`          | One `.txt` per project: `[addons]` dirs (every immediate subdirectory becomes a module), `[requirements]`, `[setup]`      |
| `odoo/extra-addons/`          | Per-project module trees (arbitrary structure)                                                                            |
| `odoo/custom/src/`            | `odoo/` (community source). `private/` is doodba's global addons dir, only used by the `default` instance                 |
| `odoo/auto/`                  | Generated config and logs (rw-mounted, gitignored). `odoo.conf` is doodba's global config, `odoo-<instance>.log` the logs |
| `odoo/auto/instances/`        | Per-instance environments (`addons/` symlinks + `odoo.conf`), created and removed by `odoo-cli.py`                        |
| `.env`                        | Sets `COMPOSE_PROJECT_NAME=17-0-doodba` and `PORT_PREFIX=17`                                                              |

## Dev loop

Full reference: `/odoo-cli`, or `python3 odoo-cli.py COMMAND --help`.

**Agents / scripts** — background instance:

```bash
python3 odoo-cli.py start -p myproject -d mydb -i my_module   # once per session
python3 odoo-cli.py restart -u my_module                       # after every code change
python3 odoo-cli.py restart -i my_module                       # re-install after a failure
python3 odoo-cli.py logs -f                                    # or: logs -n 50
python3 odoo-cli.py stop -p myproject                          # end; removes the environment
```

Changed `container_configs/<project>.txt`? `restart -p myproject …` rebuilds the
environment. With several instances running, add `-p PROJECT` to every command; `status`
lists them.

**Developers** — test changes in the browser via Traefik:

```bash
python3 odoo-cli.py workon myproject   # opens a shell in the container; Odoo NOT started
odoo -d mydb -i my_module              # in that shell: serve at http://myproject.odoo-17.localhost
                                       # Ctrl+C, edit, `odoo -d mydb -u my_module`; `exit` when done
```

See `python3 odoo-cli.py workon --help` (includes the Traefik prerequisites).

**Before installing** — static check, no DB, seconds:
`python3 odoo-cli.py check -p myproject -i my_module`

**Missing Python packages** — `python3 odoo-cli.py pip pandas` (ephemeral) or
`[requirements]` in the project config + `start -p`.

## Running tests

Install first, then test with `-u` (never `-i` with `--test-enable`, which runs every
dependency's tests). Always pass `-p myproject` to `exec` so `odoo` finds the project's
modules. Use a dedicated, randomly named DB.

```bash
python3 odoo-cli.py exec -p myproject -- bash -c \
  "odoo -d test_db -i my_module --stop-after-init --workers=0 --no-http \
   > /opt/odoo/auto/test-myproject.log 2>&1; echo \"exit: \$?\" >> /opt/odoo/auto/test-myproject.log"

python3 odoo-cli.py exec -p myproject -- bash -c \
  "odoo --test-enable --test-tags :MyTestClass -d test_db -u my_module \
   --stop-after-init --workers=0 --no-http \
   > /opt/odoo/auto/test-myproject.log 2>&1; echo \"exit: \$?\" >> /opt/odoo/auto/test-myproject.log"
```

Results: `./odoo/auto/test-myproject.log` (one log per project, so parallel agents don't
collide).
