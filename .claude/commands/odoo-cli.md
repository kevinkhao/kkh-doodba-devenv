---
description:
  "Reference for odoo-cli.py — start/stop/restart Odoo instances, per-project instance
  environments, workon + Traefik, dev loop, tests, pip packages. Use when user asks how
  to run, restart, or manage the Odoo process; how to install or update modules; how to
  test a change in the browser; or how to wire up a new project."
---

# odoo-cli.py Reference

`odoo-cli.py` runs Odoo processes inside the `odoo` container (which itself just runs
`sleep infinity`). Several instances can run at once, one per project (`-p`); with a
single running instance `-p` is optional. `python3 odoo-cli.py COMMAND --help` has the
details of every command.

## Commands

```bash
python3 odoo-cli.py start -p myproject -d mydb -i my_module  # background instance
python3 odoo-cli.py restart -u my_module         # after a code change (fast, no rebuild)
python3 odoo-cli.py restart -p myproject -u m    # also rebuild from container_configs/
python3 odoo-cli.py stop -p myproject            # stop; removes route + environment
python3 odoo-cli.py workon myproject             # interactive shell, see below
python3 odoo-cli.py status [-p myproject] [--json]
python3 odoo-cli.py logs [-p myproject] [-f] [-n 50]
python3 odoo-cli.py exec -p myproject -- CMD     # CMD's `odoo` uses the project's addons
python3 odoo-cli.py shell -p myproject           # Odoo REPL (DB: the instance's)
python3 odoo-cli.py check -p myproject -i my_module  # static check, no DB, seconds
python3 odoo-cli.py pip pandas                   # into the container, all instances
python3 odoo-cli.py start -d mydb                # 'default' instance: no project, :8069
```

`restart` infers `-d`/`-P` from the PID file. Re-run with `-p` after changing
`[addons]`, `[requirements]` or `[setup]`.

## `workon` — test your changes in the browser

```bash
python3 odoo-cli.py workon myproject
```

Builds the environment, writes the Traefik route and opens a shell in the container.
**Odoo is not started** — run it yourself:

```bash
odoo -d mydb -i my_module   # install and serve → http://myproject.odoo-17.localhost
                            # (new DB: admin / admin)
# Ctrl+C, edit code, then:
odoo -d mydb -u my_module   # update and serve
odoo shell -d mydb          # REPL on the same addons
exit                        # leave; cleans up unless Odoo still runs
```

In that shell `odoo` gets the project's addons, the instance port, the dev flags of
`start` (your flags win), a PID file and the instance log, so `status`, `logs`,
`restart` and `stop -p myproject` work from another terminal. Changed
`container_configs/myproject.txt`? `exit` and `workon` again.

Traefik prerequisites (once per machine): Traefik v3 on the external Docker network
`traefik`, HTTP on port 80, file provider watching `~/.traefik/dynamic`
(`--providers.file.watch=true`); `*.localhost` resolving to 127.0.0.1 (systemd-resolved
does this; otherwise `workon` prints the `/etc/hosts` line). Routes are visible in the
Traefik dashboard at http://127.0.0.1:8080.

Use `workon` for hands-on work; agents and scripts use `start -p` / `restart`.

## Instance environments

```
odoo/auto/instances/<project>/   (/opt/odoo/auto/instances/<project>/ in the container)
    addons/     symlinks to every module from container_configs/<project>.txt
    odoo.conf   doodba's odoo.conf with addons_path = <addons/>,<community addons>
```

Built by `start -p` / `restart -p` / `workon` (and by `exec -p` / `shell -p` if
missing); removed by `stop` and on leaving `workon` (kept while a `workon` shell for it
is open). Instances never see each other's modules. They do share the container's Python
packages, the Postgres server and the filestore — use distinct DB names. Two agents on
the _same_ project share one instance.

The `default` instance (no `-p`) uses doodba's global addons (community +
`odoo/custom/src/private`).

## `container_configs/<project>.txt`

```
[addons]                                      # dirs under odoo/custom/ or odoo/extra-addons/; each immediate
odoo/extra-addons/360/360_community    # subdirectory is a module; later entries
odoo/extra-addons/my_project/custom    # win on name clashes

[requirements]                                # pip install -r (under odoo/custom/ or odoo/extra-addons/)
odoo/extra-addons/my_project/requirements.txt

[setup]                                       # bash scripts run as root before pip
container_configs/my_project.sh
```

See `container_configs/EXAMPLE.txt` and `EXAMPLE.sh`.

## Ports and flags

| Instance  | Container port   | URL                                                |
| --------- | ---------------- | -------------------------------------------------- |
| `default` | 8069             | http://odoo-17.localhost or http://127.0.0.1:17069 |
| named     | 8070–8099 (auto) | http://{project}.odoo-17.localhost (Traefik only)  |

`start` runs Odoo with `-c <instance odoo.conf>`, `--workers=0`,
`--dev=reload,qweb,werkzeug,xml`, no time/memory limits, `--xmlrpc-port=PORT` and, with
`-d DB`, `--db-filter=^DB$` (the URL opens DB directly). Logs:
`./odoo/auto/odoo-{instance}.log`.

## `check`

Resolves modules and their `depends` on an addons path without a database, and reports
missing modules, missing `external_dependencies.python` packages and `excludes`
conflicts. `-p` selects the project's addons path (default: global); `-i` selects the
modules (default with `-p`: all of the project's — noisy for Enterprise). Non-zero exit
on findings.

## Tests

Install first, then test with `-u` (never `-i` with `--test-enable`); always pass `-p`:

```bash
python3 odoo-cli.py exec -p myproject -- bash -c \
  "odoo -d test_db -i my_module --stop-after-init --workers=0 --no-http \
   > /opt/odoo/auto/test-myproject.log 2>&1; echo \"exit: \$?\" >> /opt/odoo/auto/test-myproject.log"

python3 odoo-cli.py exec -p myproject -- bash -c \
  "odoo --test-enable --test-tags :MyTestClass -d test_db -u my_module \
   --stop-after-init --workers=0 --no-http \
   > /opt/odoo/auto/test-myproject.log 2>&1; echo \"exit: \$?\" >> /opt/odoo/auto/test-myproject.log"
```

Read `./odoo/auto/test-myproject.log`. `stop -p myproject` afterwards removes the
environment if no instance was running.

## Python dependencies

- Ephemeral: `python3 odoo-cli.py pip <package>` (lost on container recreate)
- Per project: `[requirements]` in `container_configs/<project>.txt`
- Permanent: `odoo/custom/dependencies/pip.txt`, then `docker compose build`
