[Lab index](README.md) | [Next: Part 2 - Find the devices in the console (~5 min)](02-find-devices.md)

# Part 1 - Install the plugin (~10 min)

## 1.1 Install

On the Management Extender host:

```bat
net stop BESProxyAgent
cd "C:\Program Files (x86)\BigFix Enterprise\Management Extender\Plugins"
git clone https://github.com/jgstew/bigfix-proxyagent-servermon.git
```

Open the cloned folder in VS Code - either **File > Open Folder** and pick
`bigfix-proxyagent-servermon`, or from the same prompt:

```bat
code bigfix-proxyagent-servermon
```

When VS Code asks **Do you trust the authors of the files in this folder?**, choose
**Yes, I trust the authors**. In Restricted Mode, VS Code disables features such as
the integrated terminal and extensions for that folder.

You will edit `settings.json` and `servermon.toml` in this window for the rest of the lab,
and you can run the remaining commands in its integrated terminal (**Terminal > New
Terminal**), which opens in the plugin folder. The folder is under `Program Files`, so if
VS Code cannot save a file, reopen VS Code as administrator.

In VS Code, open `settings.json`. The `ExecutablePath` line is `py -3`
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

## 1.2 Verify before you open the console

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

---

[Lab index](README.md) | [Next: Part 2 - Find the devices in the console (~5 min)](02-find-devices.md)
