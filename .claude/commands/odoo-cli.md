---
description:
  "Reference for odoo-cli.py — start/stop/restart Odoo instances, per-project instance
  environments, workon, dev loop, tests, pip packages. Use when user asks how to run,
  restart, or manage the Odoo process; how to install or update modules; or how to wire
  up a new project."
---

# odoo-cli.py Reference

The container runs indefinitely via `sleep infinity`. `odoo-cli.py` manages the Odoo
processes inside it. All commands are safe to run from any directory.

Several Odoo instances can run at once, each named after its project (`-p`). When only
one instance is running, `-p` is optional — commands auto-detect it.

## Quick reference

```bash
# Project instance: build environment + [setup] + pip, start Odoo in the background
python3 odoo-cli.py start -p myproject -d mydb -i my_module

# Dev loop: restart after a code fix (instance, DB and port auto-detected)
python3 odoo-cli.py restart -u my_module

# Restart a specific instance, rebuilding its environment from container_configs/
python3 odoo-cli.py restart -p samotics -u my_module

# Status (table / JSON for agents / one instance)
python3 odoo-cli.py status
python3 odoo-cli.py status --json
python3 odoo-cli.py status -p samotics --json

# Stop (removes the instance's route and environment)
python3 odoo-cli.py stop -p samotics

# Logs (auto-detects instance when only one is running)
python3 odoo-cli.py logs --follow
python3 odoo-cli.py logs -p fullavl -n 50

# Interactive: build the environment and open a shell; run `odoo ...` yourself
python3 odoo-cli.py workon myproject

# One-off commands / tests / REPL on an instance's addons
python3 odoo-cli.py exec -p myproject -- odoo -d test_db -u my_module --stop-after-init
python3 odoo-cli.py shell -p myproject          # DB defaults to the instance's

# Static check before installing — no DB, seconds not minutes
python3 odoo-cli.py check -p myproject -i my_module   # my_module on myproject's path
python3 odoo-cli.py check -p myproject                # every module myproject provides

# pip install into the container (shared by all instances, ephemeral)
python3 odoo-cli.py pip pandas xlrd

# Vanilla 'default' instance: no project, doodba's global config, port 8069
python3 odoo-cli.py start -d mydb
```

---

## Instance environments

Each named instance has its own environment, so parallel instances (e.g. one per agent)
never see each other's modules:

```
odoo/auto/instances/<project>/          (/opt/odoo/auto/instances/<project>/ in the container)
    addons/     relative symlinks to every module from container_configs/<project>.txt
    odoo.conf   doodba's odoo/auto/odoo.conf with
                addons_path = <this addons dir>,/opt/odoo/custom/src/odoo/addons
```

| Event                                | Environment                                      |
| ------------------------------------ | ------------------------------------------------ |
| `start -p` / `restart -p` / `workon` | (re)built from `container_configs/<project>.txt` |
| `exec -p` / `shell -p`               | built if missing, else reused                    |
| `restart` (no `-p`)                  | reused as-is (fast path)                         |
| `stop -p`                            | removed (unless a `workon` shell for it is open) |
| leaving `workon`                     | removed if Odoo isn't running for the instance   |

Instances started with `start -p` get `-c <instance odoo.conf>`; `workon`, `exec -p` and
`shell -p` set `ODOO_RC` to it, so any `odoo` / `click-odoo` command picks it up.

The `default` instance (no `-p`) has no environment and uses doodba's global
`/opt/odoo/auto/addons` (community + `odoo/custom/src/private`).

Shared across all instances: the container's Python packages (`pip`, `[requirements]`),
the Postgres server, and the filestore volume (use distinct DB names).

---

## `container_configs/<project>.txt`

```
[addons]
odoo/custom/extra-addons/360/360_community
odoo/custom/extra-addons/my_project/custom

[requirements]
odoo/custom/extra-addons/my_project/requirements.txt

[setup]
container_configs/my_project.sh
```

- `[addons]`: directories under `odoo/custom/`; every immediate, non-dot subdirectory
  becomes a module in the environment. Later entries win on name clashes, so list shared
  repos first and project overrides last. Listing a shared repo makes all of its modules
  available (they are only installed if you `-i` them or depend on them).
- `[requirements]`: `pip install -r` for each file (must be under `odoo/custom/`).
- `[setup]`: host-side bash scripts piped to bash **as root** in the container before
  pip runs, for system packages pip deps need (e.g. `gcc`). Template:
  `container_configs/EXAMPLE.sh`.

Lines before the first header are `[addons]`. `#` comments and blank lines are ignored.
See `container_configs/EXAMPLE.txt`.

---

## `workon` — manual, interactive instance

```bash
python3 odoo-cli.py workon myproject
```

What it does: builds the environment (plus `[setup]` / `[requirements]`), assigns the
instance port and writes its Traefik route, then opens a bash shell in the container
with a `(myproject)` prompt. **It does not start Odoo.**

What you do in that shell:

```bash
odoo -d mydb -i my_module    # run Odoo in the foreground (creates mydb if needed)
# ...Ctrl+C to stop, edit code, then:
odoo -d mydb -u my_module
odoo shell -d mydb           # REPL on the same addons
exit                         # leave the shell
```

- `odoo` is a shell function: server runs automatically get the project's config, the
  instance port, a PID file and the instance log. Output also goes to the terminal.
- From another terminal, `status`, `logs`, `stop` and `restart -p myproject` see a
  server started there. `restart` from outside replaces it with a background process.
- Changed `container_configs/myproject.txt`? `exit` and run `workon` again.
- On `exit`, the environment and route are removed unless Odoo is still running for the
  instance (e.g. restarted from outside) or another `workon myproject` shell is open.

Use `workon` for hands-on work. Agents and scripts should use `start -p` / `restart`.

---

## Default flags applied on every `start`

```
--workers=0                         single-process mode
--dev=reload,qweb,werkzeug,xml      hot-reload on file change
--limit-memory-soft=0               no memory kill in dev
--limit-time-real=9999999           no timeout
--limit-time-real-cron=9999999      no cron timeout
--xmlrpc-port=PORT                  8069 for 'default', auto-assigned 8070–8099 for named
-c /opt/odoo/auto/instances/<project>/odoo.conf   named instances only
-d DB --db-filter=^DB$              when -d is given: the instance serves only DB
```

Instance logs: `./odoo/auto/odoo-{instance}.log` on the host.

---

## Instances and ports

| Instance    | Container port | Host port (PORT_PREFIX=18) | Traefik URL                        |
| ----------- | -------------- | -------------------------- | ---------------------------------- |
| `default`   | 8069           | 18069                      | http://odoo-18.localhost           |
| named (1st) | 8070           | 18070                      | http://{project}.odoo-18.localhost |
| named (2nd) | 8071           | 18071                      | http://{project}.odoo-18.localhost |

Named ports are the lowest free one in 8070–8099, stored in the PID file and reused on
`restart`. Override with `start -p myproject -P 8075 …`.

---

## `check` — static validation before installing

Resolves the requested modules plus their transitive `depends` on the addons path the
instance would get (no database, 1–2 seconds) and reports:

- **Missing modules**: a name in `depends` (or `-i`) not found on the addons path (typo,
  missing `[addons]` entry, module not vendored).
- **Missing Python dependencies**: `external_dependencies.python` not importable. Fix
  with `pip <package>` (ephemeral) or `[requirements]` (re-run `start -p`).
- **Excludes conflicts**: two modules in the set list each other in `excludes` (Odoo's
  install-time check, done statically).

`-p` selects the addons path (the project's, via a throwaway environment that never
touches a running instance; without `-p`, the global `default` path). `-i` selects the
modules to check; without `-i`, `-p` checks every module the project provides (noisy for
big repos like Enterprise). Non-zero exit if anything is found.

---

## Dev loop

```bash
python3 odoo-cli.py start -p myproject -d mydb -i my_module   # once per session
python3 odoo-cli.py restart -u my_module                       # after each code change
python3 odoo-cli.py stop -p myproject                          # end of session
```

`restart` infers `-d` and `-P` from the PID file. Without `-p` it only relaunches Odoo
(no environment rebuild, no pip): ~10–15 seconds.

| Situation                                         | Command                  |
| ------------------------------------------------- | ------------------------ |
| Code change only                                  | `restart -u my_module`   |
| First install / install rolled back               | `restart -i my_module`   |
| Changed `[addons]`, `[requirements]` or `[setup]` | `restart -p myproject …` |
| Fresh clone or after `docker compose down`        | `start -p myproject …`   |

---

## Tests

Install first, then run tests with `-u` (never `-i` with `--test-enable`). Always pass
`-p` so `odoo` uses the project's addons:

```bash
python3 odoo-cli.py exec -p myproject -- bash -c \
  "odoo -d test_db -i my_module --stop-after-init --workers=0 --no-http \
   > /opt/odoo/auto/test-myproject.log 2>&1; echo \"exit: \$?\" >> /opt/odoo/auto/test-myproject.log"

python3 odoo-cli.py exec -p myproject -- bash -c \
  "odoo --test-enable --test-tags :MyTestClass -d test_db -u my_module \
   --stop-after-init --workers=0 --no-http \
   > /opt/odoo/auto/test-myproject.log 2>&1; echo \"exit: \$?\" >> /opt/odoo/auto/test-myproject.log"
```

Read `./odoo/auto/test-myproject.log`. If no instance was running, `stop -p myproject`
afterwards removes the environment `exec -p` created.

---

## Multi-instance

```bash
python3 odoo-cli.py start -p samotics -d samotics -i samotics_sale
python3 odoo-cli.py start -p fullavl  -d fullavl  -i account_ext

python3 odoo-cli.py status
# INSTANCE    PID     DATABASE    PORT
# samotics    12345   samotics    8070
# fullavl     67890   fullavl     8071

python3 odoo-cli.py restart -p samotics -u samotics_sale
python3 odoo-cli.py logs    -p fullavl  --follow
python3 odoo-cli.py stop    -p samotics
```

| Running instances | `-p` omitted    | Result                                     |
| ----------------- | --------------- | ------------------------------------------ |
| 0                 | `stop` / `logs` | Error: nothing to target                   |
| 0                 | `restart`       | Starts the `default` instance              |
| 1                 | any command     | Uses the one running instance              |
| 2+                | any command     | Error: lists running instances, needs `-p` |

Two agents on the _same_ project share one instance name (and environment); use
different projects, or coordinate.

---

## Python dependencies

- Ephemeral (lost on container recreate): `python3 odoo-cli.py pip <package>`
- Per project: `[requirements]` in `container_configs/<project>.txt`
- Permanent (baked into image): add to `odoo/custom/src/odoo_requirements.txt`, then
  `docker compose build && docker compose up -d`
