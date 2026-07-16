# bigfix-proxyagent-servermon

A BigFix Management Extender (Proxy Agent) plugin that monitors web servers / URLs. Each monitored URL shows up in BigFix as its own proxied device:

- The **device name** is the URL with the scheme removed (`https://example.com` → `example.com`).
- The **last report time** is the last time the URL was checked.
- The **operating system** column shows the web server's `Server` header when available (e.g. `nginx/1.25.3`).
- Virtual inspectors expose the **HTTP response code**, a **detailed check result string**, and more to analyses.

The plugin protocol is modeled on [bigfix/trask](https://github.com/bigfix/trask), rewritten in modern Python (3.11+, standard library only — no dependencies).

## How it works

The Proxy Agent drives everything: every `DeviceReportRefreshIntervalMinutes` (default **60**, i.e. hourly) it drops a `refresh` command file into a command directory and invokes this plugin. The plugin then checks every configured URL (in parallel) and writes one `<device id>.report` JSON file per URL into the output directory. The Proxy Agent ingests those reports and reports the devices to the BES root server — which is what sets each device's Last Report Time to the check time.

```
BESProxyAgent ──(refresh command)──▶ plugin/servermon.py
                                          │  reads servermon.toml
                                          │  HTTP GET each URL
                                          ▼
              ◀──(<device id>.report)── one report per URL
```

## Requirements

- A BigFix Management Extender / Proxy Agent installation (Windows)
- Python 3.11+ on the machine running the Proxy Agent, on `PATH` as `python`

## Install

```bat
net stop BESProxyAgent
cd "C:\Program Files (x86)\BigFix Enterprise\Management Extender\Plugins"
git clone https://github.com/jgstew/bigfix-proxyagent-servermon.git
net start BESProxyAgent
```

If your Management Extender is installed elsewhere, adjust the two paths in [settings.json](settings.json) (`ExecutablePath` contains the path to the plugin entry point and to the config file).

## Configure

### URLs to monitor — [servermon.toml](servermon.toml)

```toml
[settings]
timeout_seconds = 30            # per-request timeout, overridable per URL

[[urls]]
url = "https://example.com"
match = "Example Domain"        # optional: fail the check unless this string
                                # appears in the response body or headers

[[urls]]
url = "https://internal.example.local:8443/health"
timeout_seconds = 10
verify_tls = false              # for self-signed certs on internal servers
```

Notes:

- Each `[[urls]]` entry becomes one device. Two entries that differ only by scheme or a trailing slash would be the same device, so the config loader rejects them.
- `match` is a case-sensitive substring search against the response headers and the first 1 MiB of the body.
- Redirects are followed; the final response is what gets reported.
- A URL that returns HTTP 4xx/5xx, fails its `match`, or does not respond at all reports `check success = false` (an unreachable server reports response code `0`).

### Check interval — [settings.json](settings.json)

The check frequency is controlled by the Proxy Agent, not the plugin:

```json
"DeviceReportRefreshIntervalMinutes": 60
```

Default is 60 (hourly). Lower it for more frequent checks; restart `BESProxyAgent` after changing it.

## Virtual inspectors

[Inspectors/servermon.inspectors](Inspectors/servermon.inspectors) declares the device report keys as relevance inspectors:

| Inspector | Type | Example |
|---|---|---|
| `http response code` | integer | `200` (`0` = no HTTP response received) |
| `http check result` | string | `OK: HTTP 200 OK (231 ms); matched 'Example Domain' in body` |
| `check success` | boolean | `true` |
| `match found` | boolean | only present when `match` is configured |
| `url` | string | `https://example.com` |
| `response time ms` | integer | `231` |
| `last check time` | string | `Wed, 15 Jul 2026 14:00:00 -0400` (castable `as time`) |
| `servermon version` | string | `0.1.0` |
| `in proxy agent context` | boolean | `true` |

A ready-to-import analysis exposing all of these as properties is provided in [analysis.bes](analysis.bes). Its applicability relevance (`in proxy agent context` AND `exists servermon version`) keeps it relevant only on devices reported by this plugin.

Example analysis properties targeting these devices:

```
Q: http response code
Q: http check result
Q: (it as time) of last check time
```

The `http check result` string always starts with `OK:`, `FAILED:` (an HTTP response was received but the status or match check failed), or `ERROR:` (no HTTP response — DNS, TCP, TLS, or timeout failure, with the reason).

## Test without a Proxy Agent

```bash
# validate the config file
python plugin/servermon.py --config servermon.toml --validate

# check every URL once and print one line per device (exit 1 if any failed)
python plugin/servermon.py --config servermon.toml --check

# same, but print the exact device reports BigFix would ingest
python plugin/servermon.py --config servermon.toml --check --json
```

You can also simulate a Proxy Agent refresh end-to-end by writing a command file and pointing the plugin at it:

```bash
mkdir -p /tmp/pending /tmp/reports
echo '{"CommandName": "refresh", "OutputDirectory": "/tmp/reports"}' > /tmp/pending/0001.json
python plugin/servermon.py --config servermon.toml --commandDir /tmp/pending
cat /tmp/reports/*.report
```

Troubleshooting: add `--log-file <path> --log-level DEBUG` to the `ExecutablePath` in settings.json to capture a rotating log of every run.

## Develop

```bash
pip install pytest
pytest
```

The tests spin up a local HTTP server, so no network access is needed.

## Related

- https://github.com/bigfix/trask — the (outdated) reference proxy agent plugin this protocol is based on
- https://github.com/spulec/uncurl
