# bigfix-proxyagent-servermon

A BigFix Management Extender (Proxy Agent) plugin that monitors web servers / URLs. Each monitored URL shows up in BigFix as its own proxied device:

- The **device name** is the URL with the scheme removed (`https://example.com` -> `example.com`). If two monitored URLs would share that name (e.g. the http:// and https:// forms of one host), each is disambiguated with its default port (`example.com:443` and `example.com:80`).
- The **device identity** is the full URL, so `http://example.com` and `https://example.com` are two independent devices with separate history. URLs that differ only by scheme case or a trailing slash are treated as the same device.
- The **last report time** is the last time the URL actually responded, so a dead URL shows a visibly stale Last Report Time while its properties keep updating.
- The **operating system** column shows the web server's `Server` header when available (e.g. `nginx/1.25.3`).
- Virtual inspectors expose the **HTTP response code**, a **detailed check result string**, and more to analyses.

The plugin protocol is modeled on [bigfix/trask](https://github.com/bigfix/trask), rewritten in modern Python (3.11+, standard library only - no dependencies). For a general reference on how Proxy Agent plugins work (with this repo as the example), see [ProxyAgents.md](bigfix/reference-files/ProxyAgents.md).

## How it works

The Proxy Agent drives everything: every `DeviceReportRefreshIntervalMinutes` (default **60**, i.e. hourly) it drops `refresh` command files into `PendingCommands\` under the plugin folder and invokes this plugin with `--commandDir`. The plugin checks the configured URL(s) (in parallel), writes one `<device id>.report` JSON file per URL into the output directory (`DeviceReports\`), and deletes each command file to acknowledge it was processed. The Proxy Agent ingests the reports and reports the devices to the BES root server - which is what sets each device's Last Report Time to the check time.

```
BESProxyAgent --(PendingCommands\*.command)--> plugin/servermon.py
                                                   |  reads servermon.toml
                                                   |  HTTP GET each URL
                                                   v
              <--(DeviceReports\<device id>.report)-- one report per URL
```

Once devices are registered, a modern Proxy Agent (observed on 10.x) sends **per-device** refresh commands; the command file format and fields are covered in [ProxyAgents.md](bigfix/reference-files/ProxyAgents.md).

## Requirements

- A BigFix Management Extender / Proxy Agent installation (Windows)
- Python 3.11+ on the machine running the Proxy Agent, on `PATH` as `python`

The plugin runs on the Python standard library alone. One pure-Python package, [tomlkit](https://pypi.org/project/tomlkit/), is **vendored** as a wheel in [vendor/](vendor/) and loaded directly from there - no `pip install` needed. It is optional at runtime: it is used only to rewrite `servermon.toml` on `set refresh interval` / `delete device` while preserving comments, and if the wheel is missing or fails to load the plugin falls back to regex-based line editing, so nothing breaks. Updating the wheel is covered in [CONTRIBUTING.md](CONTRIBUTING.md).

## Install

```bat
net stop BESProxyAgent
cd "C:\Program Files (x86)\BigFix Enterprise\Management Extender\Plugins"
git clone https://github.com/jgstew/bigfix-proxyagent-servermon.git
net start BESProxyAgent
```

If your Management Extender is installed elsewhere, adjust the two paths in [settings.json](settings.json) (`ExecutablePath` contains the path to the plugin entry point and to the config file).

## Configure

### URLs to monitor - [servermon.toml](servermon.toml)

```toml
[settings]
timeout_seconds = 30            # per-request timeout, overridable per URL

[[urls]]
url = "https://example.com"
match = "Example Domain"        # optional: check fails unless this regex matches
no_match = "database error"     # optional: check fails if this regex matches

[[urls]]
url = "https://internal.example.local:8443/health"
timeout_seconds = 10
verify_tls = false              # for self-signed certs on internal servers
measure_network_hops = true     # optional: estimate hop count to the server
```

Notes:

- The plugin uses `servermon.toml` in the repo root (next to `plugin/`) by default. A path passed via `--config` is used if it exists; if not, the plugin falls back to the default location. The absolute path of the config actually used is logged at startup.
- The config is re-read on **every invocation** (the plugin is not a daemon), and any URL that has never been checked is reported on any refresh - even a refresh targeted at a different device - so newly added URLs appear in BigFix on the very next Proxy Agent invocation, no restart needed.
- Each `[[urls]]` entry becomes one device. Two entries that differ only by scheme or a trailing slash would be the same device, so the config loader rejects them.
- `match` and `no_match` are both case-insensitive **regexes** searched against the response headers and the first 1 MiB of the body. `match` must be found for the check to pass; a `no_match` hit fails the check even on HTTP 200 - for catching pages like "Could not connect to the database" served with a success status. Plain text works as a pattern, but regex metacharacters (`. ? * + ( ) [ ] \`) are interpreted - escape them with `\` if you mean them literally. Both are validated at config load.
- Redirects are followed; the final response is what gets reported.
- A URL that returns HTTP 4xx/5xx, fails its `match`, trips its `no_match`, or does not respond at all reports `success of http check = false` (an unreachable server reports response code `0`).
- `measure_network_hops = true` (per URL, default off) estimates the network hop count to the server by binary-searching the smallest IP TTL at which a plain TCP connect completes (~7 short connects, no TLS/HTTP involved, no elevated privileges).
  - To keep the probing cheap it rides along with only **1 in every 6** regular checks of that URL - an hourly URL is measured every 6 hours - and there is deliberately no separate interval setting. Targeted/manual refreshes never pull a measurement forward: they perform the regular check only.
  - The most recent value is re-sent in every report as `network hops of http check` (absent until the first successful measurement); a failed measurement waits a full 6-check cycle before retrying and keeps the last known value.
  - Hop counts to CDN/anycast sites measure the path to the nearest edge and routes change - treat the value as an estimate.

### Check interval - [settings.json](settings.json)

The check frequency is controlled by the Proxy Agent, not the plugin:

```json
"DeviceReportRefreshIntervalMinutes": 60
```

Default is 60 (hourly). Lower it for more frequent checks; restart `BESProxyAgent` after changing it.

Individual URLs can opt into a **longer** interval with `check_interval_minutes` in their `[[urls]]` entry:

- Until the interval has elapsed (tracked in the state file, with 10% slack for heartbeat jitter), the plugin skips the actual HTTP check and **re-submits the cached report** instead - the Proxy Agent always gets a report for every refresh (a pending action waits on one), only the URL is spared the traffic.
- The re-submitted report keeps all its cached check data (`last check time` shows when the URL was really checked) but advances `last server communication` so it counts as fresh.
- Since the plugin only runs when the Proxy Agent invokes it, a per-URL interval effectively rounds up to a multiple of the heartbeat - set `DeviceReportRefreshIntervalMinutes` to the smallest interval you need and per-URL intervals to larger values.
- Action-driven refreshes (a "check now" action) always check regardless.

### TLS trust store

For `https://` URLs (with `verify_tls` on, the default), the trusted CAs are the **combination** of:

1. The OS certificate store - on Windows, the system `ROOT` and `CA` stores - plus anything pointed to by the `SSL_CERT_FILE` / `SSL_CERT_DIR` environment variables.
2. The [certifi](https://pypi.org/project/certifi/) bundle, if the package is installed (`python -m pip install certifi`) - optional, the plugin stays stdlib-only without it.
3. A PEM bundle named `ca-bundle.pem` in the repo root next to `servermon.toml` - useful for internal/corporate CAs, or public roots missing from an isolated server's OS store. The repo ships one containing [ISRG Root X1](https://letsencrypt.org/certs/isrgrootx1.pem) (the Let's Encrypt root, needed for many public sites); append additional PEM certificates to it as needed, or delete it if unwanted - it is optional and serves as an example.

Which bundles were loaded is logged at startup (`TLS trust: loaded ...`); a bundle that fails to parse is logged and skipped rather than fatal. A `CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate` error means none of these sources contain the site's root/intermediate - drop the needed PEM into `ca-bundle.pem` or install certifi.

## Virtual inspectors

[Inspectors/servermon.inspectors](Inspectors/servermon.inspectors) declares the device report keys as relevance inspectors:

The check itself is one nested `http check` object, read with `<phrase> of http check`:

| Inspector | Type | Example |
|---|---|---|
| `url of http check` | string | `https://example.com` |
| `response code of http check` | integer | `200` (`0` = no HTTP response received) |
| `result of http check` | string | `OK: HTTP 200 OK (231 ms); matched 'Example Domain' in body` |
| `success of http check` | boolean | `true` |
| `match found of http check` | boolean | only present when `match` is configured |
| `bad string found of http check` | boolean | only present when `no_match` is configured; `true` = reachable but serving known-bad content |
| `response time ms of http check` | integer | `231` |
| `connect time ms of http check` | integer | `18` - DNS + TCP connect of the last connection made (excludes TLS); absent when nothing connected |
| `network hops of http check` | integer | `12` - only for `measure_network_hops` URLs, once measured |
| `last error of http check` | string | detail string of the most recent *failed* check |
| `last error time of http check` | time | when that error occurred |
| `tls version of http check` | string | `TLSv1.3` (absent for plain http / no connection) |
| `ssl certificate expiration of http check` | time | server cert `notAfter`, e.g. `Sat, 29 Aug 2026 21:41:26 +0000` (absent for plain http or `verify_tls = false`) |
| `remote ip address of http check` | string | `172.66.147.243` (absent when nothing connected) |

Plus the plugin-level top-level inspectors:

| Inspector | Type | Example |
|---|---|---|
| `refresh interval` | integer | effective check cadence in minutes: the URL's `check_interval_minutes`, else the heartbeat from settings.json |
| `last check time` | time | `Wed, 15 Jul 2026 14:00:00 -0400` |
| `servermon version` | string | `0.1.0` |
| `in proxy agent context` | boolean | `true` |
| `proxy agent plugin` | object | `name of it` (`servermon`), `version of it`, `host of it` (the relay), `last report time of it` |

### Built-in (reserved property) inspectors

The proxy agent ships a `Version 3` inspector list that every plugin should fill in as far as it can, feeding the reserved console properties. servermon fills the ones that make sense for a URL device:

| Built-in inspector | servermon reports |
|---|---|
| `device type` | `Web Server` |
| `dns name` | the URL's hostname (`forum.bigfix.com`) |
| `name of <operating system>` | the `Server` response header (`nginx`), else `servermon` |
| `version of <operating system>` | the TLS protocol version (`1.3`) for https, else the plugin version |
| `address of <ip interface>` (IP Address) | the remote server IP the check actually connected to |
| `ipv6 interfaces of <network adapter>` | also filled when the connected peer is IPv6 |

CPU, BIOS, drive, RAM, and logged-on-user inspectors are deliberately left unfilled (a URL has none of those); the console shows their default values. The connected peer IP and TLS version are read from the live check socket, so they reflect the actual connection, not just a DNS lookup - both are absent when the server was unreachable.

A ready-to-import analysis exposing all of these as properties is provided in [bigfix/content/analysis-servermon.bes](bigfix/content/analysis-servermon.bes). Its applicability relevance (`in proxy agent context` AND `exists servermon version`) keeps it relevant only on devices reported by this plugin. It also derives an **SSL Certificate Days Remaining** property from the expiry, handy for alerting on soon-to-expire certs.

Example analysis properties targeting these devices:

```
Q: response code of http check
Q: result of http check
Q: last check time
```

Three timestamps with distinct meanings are reported:

- `last check time` - when the plugin last checked the URL (every check, reachable or not).
- `last server communication` - also the check time; the Proxy Agent uses it as the "effective device communication time" that decides whether a report is new, so reports stay fresh every cycle regardless of file timestamps.
- `last device report time` - the last time the URL actually answered with an HTTP response (any status code; declared in the agent's own `main.inspectors`). When present, the Proxy Agent uses it to generate the console's **Last Report Time** - so a URL that stops responding shows a stale Last Report Time and eventually greys out like a dead client, while its other properties keep updating. Until a URL has responded at least once, the key is absent and Last Report Time falls back to the report time.

The `result of http check` string always starts with `OK:`, `FAILED:` (an HTTP response was received but the status or match check failed), or `ERROR:` (no HTTP response - DNS, TCP, TLS, or timeout failure, with the reason).

`last error of http check` and `last error time of http check` capture the most recent failed check even after it clears: the plugin remembers each device's last error in a small state file (`servermon-state.json` in the repo root by default, `--state-file` to relocate) and re-sends it with every report until a newer error replaces it. The keys are absent only for a device that has never failed.

## On-demand checks ("check now")

Three ways to trigger an immediate check of a device instead of waiting for the next heartbeat:

1. **Right-click the device in the console and Send Refresh.** The `ConsoleSendRefresh` notification makes the Proxy Agent issue a targeted refresh command to the plugin, which checks that URL immediately.
2. **Target the device with any action** (by ID or computer name) - the Proxy Agent automatically sends a refresh request to targeted devices when an action is detected, and another when it completes.
3. **An actionscript `refresh` command.** If the Proxy Agent delivers a refresh carrying a `commandID` (an actionscript-driven refresh), the plugin runs the check and answers with a command result of `Completed` (check passed) or `Failed` (check failed) - so the action status directly reflects the URL's health. Caveat: the Proxy Agent only runs actionscript commands listed in `ProxyPluginCommands.json` (BES Support), where `refresh` appears under other plugins' names and `servermon` has no entry - whether it delivers the command to this plugin depends on how that whitelist is scoped, so verify on a test deployment before relying on it.

The plugin also supports two more whitelisted actionscript commands (verified working on a live 10.x Proxy Agent for `set refresh interval`):

- **`set refresh interval <minutes>`** - targeted at a URL device, writes `check_interval_minutes = <minutes>` into that URL's `[[urls]]` entry in servermon.toml (comments and formatting preserved, and the edit is refused if the result would not parse). Reports `Completed` on success, `Error` for a bad argument or unknown device. Takes effect from the next plugin invocation.
- **`delete device`** - targeted at a URL device, stops monitoring it. Removal is **deferred by one refresh** (a protocol requirement - see "The action lifecycle" in [ProxyAgents.md](bigfix/reference-files/ProxyAgents.md)): the command reports `Completed`, the device is reported one last time on the next refresh, and then its `[[urls]]` entry is removed from servermon.toml (leaving `urls = []` if it was the last one) and its history dropped from the state file. With no further reports the device then expires from BigFix after `DeviceReportExpirationIntervalHours`; delete the computer from the console for immediate removal (it will not come back).

After any action command completes, the Proxy Agent sends a follow-up refresh to the device and only reports the action's final status once that refresh's device report arrives - the plugin always answers refreshes with a report (cached, if the URL is within its check interval), so action statuses post promptly.

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

## Troubleshooting

- Every run writes a rotating log (1 MiB * 3 backups) to `Logs\servermon.log` under the plugin folder by default, creating the directory if needed. Use `--log-file <path>` in the `ExecutablePath` to log somewhere else, and `--log-level DEBUG` for more detail. If the log file cannot be written (e.g. permissions), the plugin logs to stderr and keeps running.
- The Proxy Agent's own logs (and extra log streams via the registry) and the **carbon copy** client settings for capturing the raw command/report traffic are covered under "Debugging a plugin" in [ProxyAgents.md](bigfix/reference-files/ProxyAgents.md).

## Resetting the plugin's local state

The plugin's persistent local files are safe to delete - both self-heal on the next refresh. Delete them while the plugin is idle (between refreshes) and let the next refresh rebuild them:

- **`servermon-state.json`** - the plugin's per-device memory (last check time, last error, last URL contact, network hops, cached reports, and any deferred deletion). Deleting it makes every device look new: the next refresh performs a real check of every URL regardless of its `check_interval_minutes`, and **last error**, **last URL contact**, and **network hops** stay blank in the console until each re-occurs (a new failure, the next successful response, and the next hops measurement respectively).
- **`DeviceReports\*.report`** - the report files the Proxy Agent ingests. Deleting them loses nothing persistent; the next refresh regenerates them. (If you delete one the agent has not yet ingested, only that one refresh's data for that device is lost.)

Two things this does **not** do:

- It does **not** remove devices from the BigFix console. Devices already reported live on the BES root server, not in these files; they persist (showing their last-posted data) until a fresh report updates them, they go silent for `DeviceReportExpirationIntervalHours` and expire, or you delete the computer in the console.
- Any pending **delete device** that had not yet finalized is silently canceled - the deferred-deletion flag lived only in `servermon-state.json`.

## Develop

```bash
pip install pytest
pytest
```

The tests spin up a local HTTP server, so no network access is needed.

## Related

- https://gist.github.com/jgstew/671bc55470e3afdf5bfa1fa547e1c08c
- https://github.com/spulec/uncurl
