# Contributing

Dev-facing notes for humans and coding agents. For what the plugin *does* and
how it deploys, see [README.md](README.md); this file covers how to work on it
and the non-obvious invariants that break things if ignored.

## Setup and checks

Python 3.11+ (needs stdlib `tomllib`). No install step is required to run the
plugin, but development uses a few tools:

```bash
python -m pip install pytest pre-commit
pytest                 # full suite; fast, no network (a local HTTP server is used)
pre-commit run -a      # flake8, isort, codespell, etc. (see .pre-commit-config.yaml)
```

Tool config lives in `pyproject.toml` (`[tool.flake8]`, `[tool.pylint.*]`,
`[tool.codespell]`, `[tool.pytest.ini_options]`). `pythonpath = ["."]` there is
why `import servermon` works from the repo root without installing. flake8 reads
`[tool.flake8]` via the `Flake8-pyproject` plugin (declared in the pre-commit
hook); a bare `pip`-installed flake8 needs that plugin too.

There is no CI or Makefile; these checks are run by hand. Agents: see
[AGENTS.md](AGENTS.md) for the definition of done.

## Where things live

Each module in `servermon/` has one job:

| Module | Responsibility |
|---|---|
| `cli.py` | Arg parsing, config/log/state path resolution, entry point (`main`). |
| `plugin.py` | The command loop: dispatch refresh / action commands, write reports & results. |
| `command.py` | Parse one Proxy Agent `.command` file (case-insensitive keys). |
| `checker.py` | Perform one HTTP(S) check; owns the TLS trust chain and peer-IP capture. |
| `device.py` | Device identity (id/name from URL) and building a `.report` dict. |
| `config.py` | Load/validate `servermon.toml`; edit it for `set refresh interval` / `delete device`. |
| `state.py` | Per-device history persisted across runs (`servermon-state.json`). |
| `util.py` | Atomic file writes. |
| `_vendor.py` | Load the vendored tomlkit wheel from `vendor/`. |

`plugin/servermon.py` is just a `sys.path` shim so the Proxy Agent can launch the
package from a checkout. Tests import `servermon` directly.

## Invariants (break these and the plugin misbehaves in ways tests may not catch)

These come from the real Proxy Agent protocol, not from taste:

- **A device report fully replaces the device's prior data in BigFix** (see
  "Device reports" in
  [ProxyAgents.md](bigfix/reference-files/ProxyAgents.md)): anything that must
  survive a check with no fresh value (last error, last URL contact, refresh
  interval) is re-sent on *every* report via `state.py` - omitting a key
  clears it.
- **`tomllib` is the source of truth for what will parse.** Every config write
  (tomlkit or the regex fallback) is re-parsed with stdlib `tomllib` before being
  committed (`_write_validated_config_text`). Keep it that way; tomlkit can emit
  things the plugin's own loader would treat differently.
- **Device identity is the scheme-less, trailing-slash-stripped URL** (`device.device_name` -> sha256 = `device_id`). `http://x/` and `https://x` are the same device; the config loader rejects such collisions.
- **A refresh must always answer with a report** (even a cached replay) -
  otherwise pending actions hang (see "The action lifecycle" in
  [ProxyAgents.md](bigfix/reference-files/ProxyAgents.md)). This is why
  `delete device` is *deferred*. Never make a refresh path return zero reports
  for a device the agent still knows about.
- **Command result files** are `<commandID>-<PID>-<seq>.json`; refreshes with a
  `commandID` are action-driven and expect a command result, not device reports.
- **The state file is merge-on-save**: `save()` re-reads and overlays only this
  instance's changes, because the agent may run plugin instances concurrently.
  Don't replace it with a whole-file rewrite.

## Testing conventions

- `tests/conftest.py` starts a local `http_server` (paths: `/ok`, `/redirect`,
  `/error`, `/flaky`, everything else 404) and a `closed_port_url`. No test hits
  the network - keep it that way; add endpoints to the fixture instead.
- Config-write tests use the `write_backend` fixture (in `test_config.py`) to run
  **both** the tomlkit and regex-fallback paths. Any change to
  `set_url_check_interval` / `remove_url_entry` must pass under both.
- Prefer driving behavior through `ServerMonPlugin.process_command_dir` with a
  written command file (see `test_plugin.py`) over calling internals - it exercises
  the real dispatch and file lifecycle.
- To seed cross-run state, write `servermon-state.json` before constructing the
  plugin (see the interval/delete tests). Keys: `last check`, `last contact`,
  `last error`, `last report`, `pending deletion`.

## Editing gotchas

- **Vendored tomlkit** (`vendor/tomlkit-*.whl`) is optional at runtime: reads use
  stdlib `tomllib`, and if the wheel is missing the config writers fall back to
  regex line editing. To bump it, drop a newer wheel in `vendor/` (newest by
  filename wins) and delete the old one.
- `__version__` in `servermon/__init__.py` is reported to BigFix as
  `servermon version` and drives the OS-version fallback. The when-to-bump rule
  lives in [AGENTS.md](AGENTS.md) ("Definition of done").
- New reportable data means editing **three** places in lockstep: emit it in
  `device.build_report`, declare its type in `Inspectors/servermon.inspectors`,
  and (if it should be visible in the console) add a property to
  `analysis-servermon.bes`. The `.inspectors` and `.bes` files require a
  `BESProxyAgent` restart / analysis re-import to take effect.
