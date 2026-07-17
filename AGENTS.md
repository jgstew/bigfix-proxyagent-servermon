# AGENTS.md

Guardrails for coding agents. Read [README.md](README.md) (what the plugin does,
how it deploys) and [CONTRIBUTING.md](CONTRIBUTING.md) (dev workflow, module map,
invariants, testing) first - this file does not repeat them, it only states the
imperatives an agent must follow that those docs describe descriptively.

## Definition of done

There is no CI to catch a regression after you stop. Before treating any change
as complete:

- `pytest` passes (fast, no network).
- `pre-commit run -a` is clean.
- If you changed runtime behavior, verify it end-to-end by driving the plugin
  with a written `.command` file (README -> "Test without a Proxy Agent"), not by
  unit tests alone.

## Guardrails

- The **invariants** in CONTRIBUTING are protocol constraints, not style. Treat
  them as correctness requirements.
- Stay **stdlib-only**. The vendored tomlkit wheel is the sole exception and must
  never be added to `[project.dependencies]`.
- A change to reported data must stay in sync across code, inspectors, and
  analysis together (CONTRIBUTING -> "Editing gotchas" lists the three files).
- Runtime artifacts (`Logs/`, `servermon-state.json`, `__pycache__/`) are
  gitignored - don't commit them.
