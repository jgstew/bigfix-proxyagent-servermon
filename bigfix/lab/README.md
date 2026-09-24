# Lab: monitor a website with BigFix

A ~60 minute hands-on lab for BigFix administrators. You will install the servermon
Proxy Agent plugin, monitor a few URLs with it, and then manage the whole thing from
the BigFix console without touching the plugin host again.

No Python knowledge is required.

This file does not repeat the main [README.md](../../README.md) - it links to it. Keep the
main README open in a second window.

**Contents**

- [Part 0 - Before the lab (instructor)](#part-0---before-the-lab-instructor)
- [Part 1 - Install the plugin (~10 min)](#part-1---install-the-plugin-10-min)
- [Part 2 - Add URLs to monitor (~10 min)](#part-2---add-urls-to-monitor-10-min)
- [Part 3 - Run it all from the console (~25 min)](#part-3---run-it-all-from-the-console-25-min)
- [Part 4 - Pick one (~10 min)](#part-4---pick-one-10-min)
- [Troubleshooting](#troubleshooting)

---

## Part 0 - Before the lab (instructor)

Students should spend the hour on the plugin, not on prerequisites. Stage this first.

**Per student (or pair):**

- A Windows machine with the BigFix **Management Extender / Proxy Agent** installed,
  reporting to the root server, and RDP access to it.
- **Python 3.11+** installed and on `PATH` as `python` (check: `python --version`).
- A BigFix console login with rights to **create custom content** (tasks, analyses) and
  to target the proxied devices.

**Speed up the clock.** The plugin only runs when the Proxy Agent invokes it. The shipped
`DeviceReportRefreshIntervalMinutes` in [settings.json](../../settings.json) is **10** - at that
cadence a student sees about one refresh in the whole lab. Before the lab, set it to `5`
and restart the service:

```bat
net stop BESProxyAgent
net start BESProxyAgent
```

Also teach students, early and loudly, to right-click a device in the console and choose
**Send Refresh** to force an immediate check rather than waiting for the heartbeat.

**Lab URLs.** Have two reachable URLs ready that are not on the public internet if the lab
network is isolated - an internal web page and, ideally, one the instructor can break
on purpose (stop the service, or change the page text) for a live demo of a failing check.

---

## Part 1 - Install the plugin (~10 min)

### 1.1 Install

On the Management Extender host:

```bat
net stop BESProxyAgent
cd "C:\Program Files (x86)\BigFix Enterprise\Management Extender\Plugins"
git clone https://github.com/jgstew/bigfix-proxyagent-servermon.git
```

Open `bigfix-proxyagent-servermon\settings.json`. The `ExecutablePath` line is `py -3`
plus one absolute path, to `plugin\servermon.py`. Fix that path if the Management
Extender is installed anywhere other than the default location. The interpreter itself
is not hardcoded - the `py` launcher finds it - so check it is there first:

```bat
py -3 --version
```

Then:

```bat
net start BESProxyAgent
```

### 1.2 Verify before you open the console

Get in the habit of testing the plugin directly - it answers in seconds, while the console
answers in minutes. From the plugin folder:

```bat
py -3 plugin\servermon.py --config servermon.toml --validate
```

That parses [servermon.toml](../../servermon.toml) and reports any configuration error. Then
actually check every URL once:

```bat
py -3 plugin\servermon.py --config servermon.toml --check
```

One line per URL, and a non-zero exit code if any check failed. Neither command needs the
Proxy Agent to be running.

### 1.3 Find the devices in the console

Within one heartbeat, the URLs from the shipped `servermon.toml` appear in **Computers**
as ordinary-looking devices:

- **Device Type** is `Web Server`.
- The **OS** column shows the web server's `Server` header (for example `nginx/1.25.3`).
- The device name is the URL with the scheme removed - `https://example.com` becomes
  `example.com`.

### 1.4 Import the analysis

Import `bigfix/content/analysis-servermon.bes` into the console and **activate** it. It
exposes everything the plugin reports as properties: response code, check result,
response time, TLS version, certificate expiry and more.

> **Checkpoint 1** - you have a device named `example.com` whose *HTTP Response Code* is
> `200` and whose *HTTP Check Result* starts with `OK:`.

**What to notice**

- One `[[urls]]` entry in `servermon.toml` equals exactly one device in BigFix.
- Device identity is the **full URL**, so `http://example.com` and `https://example.com`
  are two separate devices with separate history.
- **Last Report Time** is the last time the URL actually *answered*. A URL that stops
  responding goes stale and eventually greys out like a dead client, while its other
  properties keep updating. That is deliberate - it makes a dead site look dead.

---

## Part 2 - Add URLs to monitor (~10 min)

Edit `servermon.toml` on the plugin host and add two entries at the end - one that should
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
> `FAILED:`. Leave it broken - you will repair it from the console in Part 3.

**What to notice**

- You did **not** restart anything. `servermon.toml` is re-read on every single invocation
  of the plugin, and a URL that has never been checked is reported on the next refresh -
  so new URLs show up immediately. By contrast, `settings.json` belongs to the Proxy Agent
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

## Part 3 - Run it all from the console (~25 min)

**From here on, pretend you have no RDP access to the Management Extender host.**

Everything you did in Part 2 - adding a URL, changing a match string, changing a cadence,
removing a URL - can be done as a BigFix action targeted at the virtual device. That is
the interesting part of this plugin, and it is how you would actually run it when the
plugin host belongs to another team.

### 3.1 Import the tasks

Import all four tasks from [bigfix/content/](../content/):

| Task | Actionscript it runs |
|---|---|
| `ServerMon ProxyAgent_ add url.bes` | `push link <url>` |
| `ServerMon ProxyAgent_ set url option.bes` | `set <field> <value>` |
| `ServerMon ProxyAgent_ set refresh interval.bes` | `set refresh interval <minutes>` |
| `ServerMon ProxyAgent_ Delete Virtual Device.bes` | `delete device` |

Look at their relevance: `in proxy agent context` and `exists servermon version`. That
pair keeps these tasks relevant only on devices this plugin reports, so they can never be
run against a real computer by accident.

### 3.2 Why is the add-URL command called `push link`?

Because a Proxy Agent plugin **cannot invent new actionscript command names**. The Proxy
Agent only forwards commands listed in `ProxyPluginCommands.json` (published on the BES
Support site) - a central whitelist that your plugin cannot add itself to. In practice, a
command listed there for *any* plugin gets delivered, so servermon borrows an existing
whitelisted name, `push link`, and treats its argument as the URL to add.

Worth remembering when you write your own plugin: implement commands BigFix already
knows, or the agent will never hand them to you.

### 3.3 Add a URL from the console

Make a copy of the **add url** task and change its actionscript to a URL of your choosing:

```
push link https://<a URL you pick>
```

Target it at **any** servermon device. The target genuinely does not matter here - the URL
comes from the argument, and there is no "plugin-level" device to aim at.

Run it, and watch the action status go to **Completed**. On the next refresh the new
device appears in the console.

### 3.4 Repair the broken device from the console

This is the one to slow down for. Take the device you deliberately broke in Part 2.

Copy the **set url option** task, target that device, and set the actionscript to the text
that really is on the page:

```
set match <the text that is actually on the page>
```

Run it, then **Send Refresh** on that device. *Check Success* flips to `True`.

You just fixed a monitoring check without logging into the server that runs the monitor.

Variations worth trying:

- `set match` with **no value** clears the match entirely, reverting to the default.
- `set verify_tls false` is how you monitor an internal site with a self-signed
  certificate - but note the side effect: with TLS verification off, the plugin stops
  reporting *SSL Certificate Expires* for that URL.
- `set timeout_seconds 10`, `set no_match <bad text>`, `set measure_network_hops true`.

An unknown field or a bad value is refused: the action comes back **Error** and nothing is
written.

### 3.5 Change how often a URL is checked

Target one device with the **set refresh interval** task:

```
set refresh interval 5
```

Leave another device at a long interval (say 360). Now compare these three properties
across the two devices:

| Property | Fast device | Slow device |
|---|---|---|
| *Refresh Interval (Minutes)* | 5 | 360 |
| *Last Check Time* | recent | stale |
| *Last Report Time* | recent | recent |

The slow device is still reporting on every heartbeat - but the plugin is **replaying its
cached report** instead of hitting the URL again. That is why its last *check* time is old
while the device still looks alive. The rule: a refresh must always be answered with a
report, or pending actions against that device would hang forever.

Set the Proxy Agent heartbeat to the fastest cadence you need anywhere, then use
`refresh interval` to make individual URLs cheaper.

### 3.6 Retire a device

Target one of the URLs you added with the **Delete Virtual Device** task:

```
delete device
```

Watch what happens: the action reports **Completed**, the device is reported **one more
time** on the next refresh, and only then is its `[[urls]]` entry removed from
`servermon.toml`. That deferral is required by the protocol - see 3.5 above, a refresh
must always produce a report.

Note what this does *not* do: it stops monitoring the URL, but it does not remove the
computer from the BigFix console. The device goes silent and expires after
`DeviceReportExpirationIntervalHours` (168 by default), or you delete the computer in the
console for immediate removal.

### 3.7 Read the action status

Throughout Part 3, the action status is the plugin's own answer, not just "the action
ran":

| Status | Meaning |
|---|---|
| `Completed` | the plugin did it |
| `Failed` | the external system refused - for an action-driven `refresh`, the URL check itself failed |
| `Error` | the plugin could not even try: unknown field, bad value, duplicate URL, unknown device |

> **Checkpoint 3** - now RDP back to the plugin host and open `servermon.toml`. It has
> changed since Part 2 - new entry, new match, new interval, one entry gone - and you
> never opened the file. Notice the comments and formatting are still intact.

---

## Part 4 - Pick one (~10 min)

Pick whichever is most relevant to you. These are independent.

- **Alert on down sites.** Create an automatic computer group whose relevance is
  `not success of http check`. That group is your "sites that are down" view, and anything
  BigFix can target at a group now applies to it.
- **Catch expiring certificates.** The analysis derives an *SSL Certificate Days Remaining*
  property. Sort on it, then build a group for `< 30` days.
- **Look under the hood.** On the plugin host, write a command file by hand and run the
  plugin against it - this is exactly what the Proxy Agent does:

  ```bat
  mkdir C:\temp\pending C:\temp\reports
  echo {"CommandName": "refresh", "OutputDirectory": "C:\\temp\\reports"} > C:\temp\pending\0001.json
  py -3 plugin\servermon.py --config servermon.toml --commandDir C:\temp\pending
  type C:\temp\reports\*.report
  ```

  Match the keys in the `.report` JSON to the properties you have been reading in the
  console. Then look at `Logs\servermon.log`.
- **Try the dashboard.** `bigfix/content/dashboard-servermon.ojo`.
- **Discuss.** What in your own environment would you monitor this way, and how often?
  What would you *not* monitor this way?

---

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| No devices ever appear | the plugin never ran | `Logs\servermon.log` under the plugin folder; the plugin path in `ExecutablePath` in `settings.json`; `py -3 --version` |
| Your config edit had no effect | you edited `settings.json`, not `servermon.toml` | `settings.json` is read by the service - restart `BESProxyAgent` |
| Nothing changes for half an hour | heartbeat still at the shipped value | `DeviceReportRefreshIntervalMinutes` in `settings.json`; or right-click the device and **Send Refresh** |
| Action status is `Error` | the plugin refused the argument | unknown field, bad value, or duplicate URL - `Logs\servermon.log` has the detail |
| Action never completes | the agent did not deliver the command | the `ProxyPluginCommands.json` whitelist - see [3.2](#32-why-is-the-add-url-command-called-push-link) |
| A check fails with `CERTIFICATE_VERIFY_FAILED` | the site's root CA is in none of the trust sources | README -> [TLS trust store](../../README.md#tls-trust-store); append the PEM to `ca-bundle.pem` |
| Everything looks stuck and you want a clean slate | stale local state | README -> [Resetting the plugin's local state](../../README.md#resetting-the-plugins-local-state) |
