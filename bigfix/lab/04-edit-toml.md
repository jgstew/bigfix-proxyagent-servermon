[Previous: Part 3 - Manage URLs from the console (~25 min)](03-console.md) | [Lab index](README.md) | [Next: Part 5 - Pick one (~10 min)](05-pick-one.md)

# Part 4 - Edit servermon.toml directly (advanced, ~10 min)

Everything in Part 3 can also be done by editing
[`servermon.toml`](../../servermon.toml) by hand on the plugin host. It is quicker for bulk
changes, but nothing checks your work until the plugin next reads the file - and a
mistake that makes the file invalid stops monitoring for **every** URL, not just the one
you edited. Prefer the console tasks; use this when you need to.

## 4.1 Add an entry by hand

In VS Code, add one entry at the end of [`servermon.toml`](../../servermon.toml) and save:

```toml
[[urls]]
url = "https://www.iana.org/help/example-domains"
match = "Example Domains"
no_match = "Could not connect to the database"
```

## 4.2 Validate before the plugin runs

Check your work in the VS Code terminal before the next refresh picks it up:

```bat
py -3 plugin\servermon.py --config servermon.toml --validate
py -3 plugin\servermon.py --config servermon.toml --check
```

`--validate` reports any configuration error; `--check` checks every URL once. If either
complains, fix the file before you do anything else.

Then **Send Refresh** on any servermon device and look for the new device.

> **Checkpoint 4** - the new device exists in the console and *Check Success* is `True`.

**What to notice**

- You did **not** restart anything - the plugin re-reads the file every time it runs. By
  contrast, [`settings.json`](../../settings.json) belongs to the Proxy Agent service and
  **does** require a restart.
- The console tasks from Part 3 refuse bad input before writing anything; a hand edit
  does not get that protection. Always run `--validate` after editing.

See the README's [URLs to monitor](../../README.md#urls-to-monitor---servermontoml) section for
the full set of per-URL options (`timeout_seconds`, `verify_tls`, `expected_status`,
`refresh_interval_minutes`, `measure_network_hops`).

---

[Previous: Part 3 - Manage URLs from the console (~25 min)](03-console.md) | [Lab index](README.md) | [Next: Part 5 - Pick one (~10 min)](05-pick-one.md)
