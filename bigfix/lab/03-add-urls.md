[Previous: Part 2 - Find the devices in the console (~5 min)](02-find-devices.md) | [Lab index](README.md) | [Next: Part 4 - Run it all from the console (~25 min)](04-console.md)

# Part 3 - Add URLs to monitor (~10 min)

Edit [`servermon.toml`](../../servermon.toml) on the plugin host and add two entries at the end - one that should
pass, and one that is **wrong on purpose**:

```toml
[[urls]]
url = "https://<your-lab-site>/"
match = "<some text that really is on that page>"

[[urls]]
url = "https://<your-lab-site>/some-page"
match = "Welcome back"          # deliberately NOT on the page - this check will fail
```

Check your work locally, without waiting for a refresh:

```bat
py -3 plugin\servermon.py --config servermon.toml --validate
py -3 plugin\servermon.py --config servermon.toml --check
```

The second entry should report a failure. Now go to the console, right-click one of the
servermon devices and **Send Refresh**, and look at the new devices.

> **Checkpoint 2** - both new devices exist in the console. The second shows
> *Check Success* `False`, *Match Found* `False`, and a *HTTP Check Result* starting with
> `FAILED:`. Leave it broken - you will repair it from the console in Part 4.

**What to notice**

- You did **not** restart anything. [`servermon.toml`](../../servermon.toml) is re-read on every single invocation
  of the plugin, and a URL that has never been checked is reported on the next refresh -
  so new URLs show up immediately. By contrast, [`settings.json`](../../settings.json) belongs to the Proxy Agent
  service and **does** require a restart.
- `match` and `no_match` are case-insensitive **regular expressions**, not plain text.
  They are searched against the response headers and the first 1 MiB of the body. Plain
  words work fine, but `.` `?` `*` `+` `(` `)` `[` `]` are regex metacharacters - escape
  them with a backslash if you mean them literally.
- Two different failure prefixes, and the difference matters when you are on the phone
  with a site owner:
  - `FAILED:` - the server answered, but the check rules said no (bad status code, the
    `match` was missing, or a `no_match` string was present).
  - `ERROR:` - no HTTP response at all: DNS, TCP, TLS or timeout. These report
    *HTTP Response Code* `0`.
- `no_match` is the one people underestimate: it fails a check that returned HTTP 200 but
  served a page saying "Could not connect to the database". Up is not the same as working.

See the README's [URLs to monitor](../../README.md#urls-to-monitor---servermontoml) section for
the full set of per-URL options (`timeout_seconds`, `verify_tls`, `expected_status`,
`refresh_interval_minutes`, `measure_network_hops`).

---

[Previous: Part 2 - Find the devices in the console (~5 min)](02-find-devices.md) | [Lab index](README.md) | [Next: Part 4 - Run it all from the console (~25 min)](04-console.md)
