# AGENTS.md

Guardrails for coding agents. Read [README.md](README.md) (what the plugin does,
how it deploys) and [CONTRIBUTING.md](CONTRIBUTING.md) (dev workflow, module map,
invariants, testing) first - this file does not repeat them, it only states the
imperatives an agent must follow that those docs describe descriptively.

## Test-driven development

Work test-first. For each behavior change:

- Write the failing test **before** the code that satisfies it.
- Run it and confirm it fails for the expected reason *before* changing the code
  (a test that passes before your change proves nothing).
- Then write the code and confirm the same test passes, with the rest of the
  suite still green.

If a test turns out to be wrong - it encoded a mistaken assumption about how the
code should behave - fixing the test is allowed, but **call it out explicitly**:
say which test changed and why the original assertion was incorrect, so a
reviewer can tell a corrected assumption from a silently weakened test.

## Definition of done

There is no CI to catch a regression after you stop. Before treating any change
as complete:

- `pytest` passes (fast, no network).
- `pre-commit run -a` is clean.
- If you changed runtime behavior, verify it end-to-end by driving the plugin
  with a written `.command` file (README -> "Test without a Proxy Agent"), not by
  unit tests alone.
- Bump `__version__` in [servermon/__init__.py](servermon/__init__.py) when you
  change the code. If that file is already uncommitted with a version bump since
  the last commit, that bump covers your change too - don't bump it again.

## Guardrails

- The **invariants** in CONTRIBUTING are protocol constraints, not style. Treat
  them as correctness requirements.
- Stay **stdlib-only**. tomlkit (bundled inside the vendored SDK wheel) is the
  sole exception and must never be added to `[project.dependencies]`.
- A change to reported data must stay in sync across code, inspectors, and
  analysis together (CONTRIBUTING -> "Editing gotchas" lists the three files).
- Runtime artifacts (`Logs/`, `servermon-state.json`, `__pycache__/`) are
  gitignored - don't commit them.
