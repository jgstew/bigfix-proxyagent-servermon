# How BigFix Proxy Agents Work

A generic reference for the BigFix Proxy Agent (Management Extender) plugin
architecture, using this repo - servermon, a URL-monitoring plugin - as the
running example. Everything here was validated against a live Proxy Agent
deployment; where the modern agent differs from the older public docs
( [bigfix/trask](https://github.com/bigfix/trask) and an
[Outdated Proxy Agent Doc](https://gist.github.com/dexdexdex/ecfead7a748993ce9715) ),
the differences are called out.

For servermon-specific usage see [README.md](README.md); for developing this
codebase see [CONTRIBUTING.md](CONTRIBUTING.md).

## The big picture

BigFix normally manages a device by running a native BES Client on it. Some
"devices" cannot run an agent - mobile devices, ESXi hosts, cloud instances,
or (here) a URL. The **Proxy Agent** (`BESProxyAgent`, a Windows service that
runs alongside a BES Relay) fills that gap: it performs registration,
relevance evaluation, and report submission *on behalf of* such devices, so
they appear in the console like any other computer.

The Proxy Agent knows nothing about the devices themselves. That knowledge
lives in **plugins**: executables the agent launches to translate between its
file-based protocol and whatever the external system speaks. servermon's
"external system" is HTTP itself - each monitored URL becomes one proxied
device.

```
BES Server <-> Relay <-> BESProxyAgent <-(command files)-> plugin <-> external system
                                       <-(report/result files)-      (here: HTTP GETs)
```

One proxy agent hosts many plugins; one plugin manages many devices. The agent
tracks which plugin owns which device and never runs two plugin instances
against the same device at once (it may run instances concurrently for
*different* devices - plugins must tolerate that, e.g. servermon's state file
is merge-on-save).

## Anatomy of a plugin

A plugin is a folder under the Management Extender's `Plugins\` directory.
Installing one is: copy the folder, restart `BESProxyAgent` (plugins register
at service startup only). This repo *is* such a folder:

```
Plugins\bigfix-proxyagent-servermon\
  settings.json                   <- required: tells the agent how to run the plugin
  Inspectors\servermon.inspectors <- declares the plugin's relevance inspectors
  plugin\servermon.py             <- the executable (any language; here Python)
  servermon.toml, vendor\, ...    <- plugin-private files; the agent ignores them
  PendingCommands\                <- created by the agent: command files appear here
  DeviceReports\                  <- created by the agent: plugin writes .report files here
```

### settings.json

The only file the agent requires. servermon's, annotated:

- `ID` - unique plugin name; becomes the device's association with this plugin.
- `ExecutablePath` - full command line to launch the plugin. The agent appends
  `--configOptions "<ConfigurationOptions>" --commandDir "<dir>"` at run time.
- `CommandFormat` - `"JSON"` (command files as JSON documents; the legacy
  `"CommandLine"` format is deprecated).
- `DeviceReportRefreshIntervalMinutes` - the **heartbeat**: how often the agent
  asks the plugin to refresh all device data. This is the cadence of everything
  "periodic" for proxied devices (property re-evaluation, policy re-application).
- `DeviceReportExpirationIntervalHours` - a device that stops reporting for
  this long is dropped by the agent. Silence is how proxied devices die.
- `TargetHintRelevance` - relevance the agent evaluates per device and passes
  to the plugin as `targetHint` with each command (servermon uses `"url"`).
- `SendSettingsToPlugin` - whether `setting` / `setting delete` actionscript
  commands are forwarded to the plugin (boolean; servermon: `false`).
- `HandlePartialRefresh` - whether the plugin accepts per-device (targeted)
  refreshes rather than only refresh-all.

## The execution model: fire and forget

The plugin is **not a daemon**. The agent writes one or more *command files*
into a directory, launches the plugin process pointing at it, and moves on
without waiting. The plugin's contract:

1. Process **every** command file currently in the directory.
2. Respond to each by writing files (reports or results) into that command's
   `outputDirectory`.
3. Delete each command file to acknowledge it.
4. Exit.

Because a fresh process runs each time, plugins re-read their own config every
invocation (which is why adding a URL to servermon.toml needs no restart) and
must persist anything that has to survive between runs themselves (servermon:
`servermon-state.json`).

### Command files

JSON documents with case-insensitive keys. Two fundamental kinds:

**Refresh** - "send me device report(s)". Observed live on 10.x, heartbeat and
notification-driven refreshes arrive as *per-device* files named
`Refresh-<device id>.command`:

```json
{"outputDirectory": "...\\DeviceReports",
 "targetDevice": "2321c64a07e0...",
 "commandName": "refresh",
 "requiredProperties": ["check success", "http response code", "..."],
 "deviceReportSequence": 2}
```

- No `targetDevice` means refresh **all** devices (also how the plugin should
  introduce brand-new devices - and plugins may submit unrequested reports at
  any time, which servermon exploits to pick up newly configured URLs on any
  invocation).
- `requiredProperties` is advisory (which inspectors the agent wants filled);
  servermon always reports everything.
- `deviceReportSequence` is a per-device report counter; echo it back in the
  report.

**Action** - "run this actionscript command on this device". Any
`commandName` other than `refresh` is an action, carrying a `commandID`, the
`targetDevice`, and `commandArguments` (the rest of the actionscript line).
The agent only forwards command names listed in `ProxyPluginCommands.json`
(in BES Support) - a central whitelist a custom plugin cannot extend, but in
practice (verified live) commands listed there for *any* plugin are delivered,
which is how servermon supports `refresh`, `set refresh interval <minutes>`,
and `delete device`.

One wrinkle: a **refresh carrying a `commandID`** is an action-driven refresh.
Its `outputDirectory` is the action-results directory, and it expects a
command *result*, not device reports.

### Command results

For every action the plugin writes a JSON result file into `outputDirectory`:

```json
[{"CommandID": "1334104551-0", "DeviceID": "2321c64a...", "Result": "Completed"}]
```

- `Result` is `Completed` (worked), `Failed` (the external system refused),
  or `Error` (the plugin could not even try - servermon's answer to any
  unsupported command).
- Name results `<commandID>-<PID>-<seq>.json` so concurrent instances never
  collide. Only `.json` files are read - write to a temp name, then rename.
- Once placed, the file belongs to the agent: never modify or delete it.

## Device reports

The heart of the protocol. In response to refreshes, the plugin writes one
`<device id>.report` JSON file per device into `DeviceReports\`. The agent
registers each new device id it sees, evaluates all subscribed fixlet/analysis
relevance against the report data, and posts a client report to the relay -
exactly what a native BES Client would do.

Three keys are mandatory (the agent cannot register the device without them):

```json
{"device id": "2321c64a07e0...",     "computer name": "localhost:42444/fake2.html",
 "data source": "servermon", "...": "everything else is inspector data"}
```

- **`device id`** - any unique string, chosen and kept stable by the plugin.
  servermon derives it deterministically (sha256 of the scheme-less URL) so no
  identity database is needed; trask, generating random ids, needed SQLite.
- The rest of the keys feed the plugin's **inspectors** (below), plus a few
  the agent itself understands:
  - `last server communication` - the "effective device communication time";
    a report is only *new* if this advances (file mtime is the fallback).
  - `last device report time` - if present, becomes the console's **Last
    Report Time**. servermon sets it to the last time the URL actually
    answered, so a dead URL's Last Report Time goes visibly stale while its
    properties keep updating via `last server communication`.
  - `deviceReportSequence` / `device report sequence` - echo of the refresh's
    sequence number.
  - Reserved-property inspectors from the agent's own `main.inspectors`
    (`device type`, `dns name`, `operating system`, the `network` structure
    for the IP Address column, ...) - fill in whatever makes sense.

Two rules with sharp edges:

- **A report fully replaces the device's previous data.** There is no merging.
  Anything that must remain visible (servermon: last error, last URL contact)
  must be re-sent in every report, which forces the plugin to remember it.
  Need to verify this.
- Write atomically (temp file, then rename to `.report`) - the agent watches
  the directory and may read the moment the file appears.

## Inspectors: exposing data to relevance

A `.inspectors` file is a list of `phrase: type` declarations mapping report
keys to relevance phrases - a small data store dressed up as inspectors:

```
http response code: integer
ssl certificate expires: time     <- JSON string, implicitly cast (MIME date)
check success: boolean
```

Plurals use JSON arrays (`cheeses: plural string`), complex types use nested
objects (`address of <ip interface>: ipv4or6 address` reads
`network.ip interfaces[].address`), and any type castable from
string/int/float/bool works (`time` being the most useful). A key omitted from
a report makes `exists <phrase>` false - servermon uses that for
"no error recorded" semantics. Content should guard with
`in proxy agent context AND exists <plugin-specific inspector>` so it is never
relevant on real BES clients (see
[bigfix/content/analysis-servermon.bes](bigfix/content/analysis-servermon.bes)).

## The action lifecycle (and its one big trap)

When an operator targets a proxied device with an action:

1. The agent sends a targeted **refresh** first (fresh data for applicability).
2. If relevant, each plugin-handled actionscript line becomes a `.command`
   file; the agent waits for the command **result** before the next line.
3. After the last line, the agent sends **another refresh** - and only when
   that refresh's device report arrives does the action leave "running".

Step 3 is the trap: *any* code path where a refresh produces no report for a
device the agent still knows about will hang actions. servermon hit this
twice - per-URL check intervals now *replay a cached report* instead of going
silent, and `delete device` *defers* removal until one final report has been
sent, only then dropping the URL from config.

Device deletion generally: there is no "forget this device" message a plugin
can send. Stop reporting and the device expires after
`DeviceReportExpirationIntervalHours` (the agent keeps sending targeted
refreshes for it until then - answer them with nothing), or delete the
computer in the console for immediate effect.

## Debugging a plugin

- The agent's log: `Management Extender\__Logs`; more streams (debug,
  evaluation, timing) via registry `HKLM\Software\BigFix\ProxyAgent` ->
  `EnabledLogs`. The plugin keeps its own log (servermon: `Logs\servermon.log`).
- **Carbon copy** client settings preserve the protocol traffic for
  inspection: `_ProxyAgent_Command_CarbonCopyPath` (commands before the plugin
  consumes them), `_ProxyAgent_CommandResults_CarbonCopyPath`,
  `_ProxyAgent_Reporting_CarbonCopyPath` (the client reports posted upstream).
- No agent required: any plugin following this protocol can be driven by hand
  by writing a command file and invoking the executable with `--commandDir`
  (README -> "Test without a Proxy Agent"), which is also how this repo's
  test suite exercises servermon end to end.
