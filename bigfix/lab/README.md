# Lab: monitor a website with BigFix

A ~60 minute hands-on lab for BigFix administrators. You will install the servermon
Proxy Agent plugin, monitor a few URLs with it, and then manage the whole thing from
the BigFix console without touching the plugin host again.

No Python knowledge is required.

This file does not repeat the main [README.md](../../README.md) - it links to it. Keep the
main README open in a second window.

## Before you start

### The moving parts

BigFix normally manages a computer through the BES Client running on it. Some things
cannot run a client - in this lab, a website. A Proxy Agent stands in for them:

- **Management Extender** - the machine that hosts the Proxy Agent: a BES Relay with the
  `BigFix Enterprise\Management Extender\` folder installed. In this lab it is also called
  the *plugin host*.
- **Proxy Agent** - the `BESProxyAgent` Windows service on that machine. It does the
  client's job on behalf of devices that cannot run one - registering them, evaluating
  relevance, sending their reports - and it launches plugins to do the actual work.
- **Plugin** - a folder under `Management Extender\Plugins\` that knows how to talk to one
  kind of external system. The Proxy Agent finds plugins only when the service starts,
  which is why Part 1 stops and restarts it. servermon is a plugin whose external system
  is HTTP.
- **Proxied (virtual) device** - what the plugin reports to BigFix. Each URL servermon
  monitors shows up in the console as its own device, alongside your real computers,
  even though no computer runs a client for it. Its properties come from the plugin's
  checks of that URL.

The README's [How it works](../../README.md#how-it-works) shows how the Proxy Agent and
the plugin pass commands and reports back and forth, and
[ProxyAgents.md](../../bigfix/reference-files/ProxyAgents.md) covers the protocol in depth.

### Already set up on the lab machine

The lab machine is the Management Extender. It already has:

- the **BigFix Proxy Agent** - installed with
  [`Install BigFix Proxy Agent (Version 11.0.6) - Customized.bes`](../content/)
  from this repository's `bigfix/content/` folder, so no lab step installs it;
- **git**, to clone the plugin;
- **Python 3** for all users, with the `py` launcher (`py -3 --version` confirms it);
- **VS Code**, to watch and edit the plugin's files;
- **internet access**, so the plugin can reach the websites it monitors and `pip` can
  download a package in Part 1;
- the **BigFix console**, which you log in to in Part 2 - so the content files you import
  are already on the same machine, in the plugin folder you clone in Part 1.

**Labs** - work through them in order; each one links to the next.

1. [Part 1 - Install the plugin (~10 min)](01-install.md)
1. [Part 2 - Find the devices in the console (~5 min)](02-find-devices.md)
1. [Part 3 - Manage URLs from the console (~25 min)](03-console.md)
1. [Part 4 - Edit servermon.toml directly (advanced, ~10 min)](04-edit-toml.md)
1. [Part 5 - Pick one (~10 min)](05-pick-one.md)
1. [Troubleshooting](troubleshooting.md)
