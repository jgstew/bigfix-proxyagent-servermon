[Previous: Part 3 - Add URLs to monitor (~10 min)](03-add-urls.md) | [Lab index](README.md) | [Next: Part 5 - Pick one (~10 min)](05-pick-one.md)

# Part 4 - Run it all from the console (~25 min)

**From here on, pretend you have no RDP access to the Management Extender host.**

Everything you did in Part 3 - adding a URL, changing a match string, changing a cadence,
removing a URL - can be done as a BigFix action targeted at the virtual device. That is
the interesting part of this plugin, and it is how you would actually run it when the
plugin host belongs to another team.

## 4.1 Import the tasks

Import all four tasks from [bigfix/content/](../content/) into the `ProxyAgents` site:

| Task | Actionscript it runs |
|---|---|
| [`ServerMon_ProxyAgent_add_url.bes`](../content/ServerMon_ProxyAgent_add_url.bes) | `push link <url>` |
| [`ServerMon_ProxyAgent_set_url_option.bes`](../content/ServerMon_ProxyAgent_set_url_option.bes) | `set <field> <value>` |
| [`ServerMon_ProxyAgent_set_refresh_interval.bes`](../content/ServerMon_ProxyAgent_set_refresh_interval.bes) | `set refresh interval <minutes>` |
| [`ServerMon_ProxyAgent_Delete_Virtual_Device.bes`](../content/ServerMon_ProxyAgent_Delete_Virtual_Device.bes) | `delete device` |

Look at their relevance: `in proxy agent context` and `exists servermon version`. That
pair keeps these tasks relevant only on devices this plugin reports, so they can never be
run against a real computer by accident.

## 4.2 Why is the add-URL command called `push link`?

Because a Proxy Agent plugin **cannot invent new actionscript command names**. The Proxy
Agent only forwards commands listed in `ProxyPluginCommands.json` (published on the BES
Support site) - a central whitelist that your plugin cannot add itself to. In practice, a
command listed there for *any* plugin gets delivered, so servermon borrows an existing
whitelisted name, `push link`, and treats its argument as the URL to add.

Worth remembering when you write your own plugin: implement commands BigFix already
knows, or the agent will never hand them to you.

## 4.3 Add a URL from the console

Make a copy of the **add url** task and change its actionscript to a URL of your choosing:

```
push link https://<a URL you pick>
```

Target it at **any** servermon device. The target genuinely does not matter here - the URL
comes from the argument, and there is no "plugin-level" device to aim at.

Run it, and watch the action status go to **Completed**. On the next refresh the new
device appears in the console.

## 4.4 Fix a failing check from the console

This is the one to slow down for. Take the device you deliberately broke in Part 3.

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

## 4.5 Change how often a URL is checked

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

## 4.6 Retire a device

Target one of the URLs you added with the **Delete Virtual Device** task:

```
delete device
```

Watch what happens: the action reports **Completed**, the device is reported **one more
time** on the next refresh, and only then is its `[[urls]]` entry removed from
[`servermon.toml`](../../servermon.toml). That deferral is required by the protocol - see 4.5 above, a refresh
must always produce a report.

Note what this does *not* do: it stops monitoring the URL, but it does not remove the
computer from the BigFix console. The device goes silent and expires after
`DeviceReportExpirationIntervalHours` (168 by default), or you delete the computer in the
console for immediate removal.

## 4.7 Read the action status

Throughout Part 4, the action status is the plugin's own answer, not just "the action
ran":

| Status | Meaning |
|---|---|
| `Completed` | the plugin did it |
| `Failed` | the external system refused - for an action-driven `refresh`, the URL check itself failed |
| `Error` | the plugin could not even try: unknown field, bad value, duplicate URL, unknown device |

> **Checkpoint 3** - now RDP back to the plugin host and open [`servermon.toml`](../../servermon.toml). It has
> changed since Part 3 - new entry, new match, new interval, one entry gone - and you
> never opened the file. Notice the comments and formatting are still intact.

---

[Previous: Part 3 - Add URLs to monitor (~10 min)](03-add-urls.md) | [Lab index](README.md) | [Next: Part 5 - Pick one (~10 min)](05-pick-one.md)
