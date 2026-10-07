[Previous: Part 5 - Pick one (~10 min)](05-pick-one.md) | [Lab index](README.md)

# Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| No devices ever appear | the plugin never ran | `Logs\servermon.log` under the plugin folder; the plugin path in `ExecutablePath` in [`settings.json`](../../settings.json); `py -3 --version` |
| Your config edit had no effect | you edited [`settings.json`](../../settings.json), not [`servermon.toml`](../../servermon.toml) | [`settings.json`](../../settings.json) is read by the service - restart `BESProxyAgent` |
| Nothing changes for half an hour | heartbeat still at the shipped value | `DeviceReportRefreshIntervalMinutes` in [`settings.json`](../../settings.json) |
| Action status is `Error` | the plugin refused the argument | unknown field, bad value, or duplicate URL - `Logs\servermon.log` has the detail |
| Action never completes | the agent did not deliver the command | the `ProxyPluginCommands.json` whitelist - see [3.2](03-console.md#32-why-is-the-add-url-command-called-push-link) |
| A check fails with `CERTIFICATE_VERIFY_FAILED` | the site's root CA is in none of the trust sources | install certifi for all users (step 1.1); or append the root's PEM to [`ca-bundle.pem`](../../ca-bundle.pem) - README -> [TLS trust store](../../README.md#tls-trust-store); see [Windows Server 2016](#windows-server-2016-and-tls) below |
| A site works in Python but not in IE or PowerShell on the server | Windows Server 2016 has no TLS 1.3 | expected - see [Windows Server 2016](#windows-server-2016-and-tls) below |
| Everything looks stuck and you want a clean slate | stale local state | README -> [Resetting the plugin's local state](../../README.md#resetting-the-plugins-local-state) |

## Check settings.json

The lab machines ship with a working [`settings.json`](../../settings.json), so you should not need this.

[`settings.json`](../../settings.json) is read by the Proxy Agent service. Its `ExecutablePath` line is `py -3`
plus one absolute path, to [`plugin\servermon.py`](../../plugin/servermon.py). Fix that path if the Management
Extender is installed anywhere other than the default location. The interpreter itself
is not hardcoded - the `py` launcher finds it - so check it is there:

```bat
py -3 --version
```

Restart the service after any change to [`settings.json`](../../settings.json):

```bat
net stop BESProxyAgent
net start BESProxyAgent
```

## Windows Server 2016 and TLS

The plugin does its own TLS with the OpenSSL built into Python, not with Windows. Windows
Server 2016 cannot do TLS 1.3 itself, but that does not affect the plugin's checks - it
only affects Windows tools such as IE and PowerShell's `Invoke-WebRequest`.

What the plugin does take from Windows is the list of trusted root certificates. Windows
Server 2016 downloads most roots only when Windows itself first needs one, so a fresh or
isolated server can be missing roots that modern sites use, and checks fail with
`CERTIFICATE_VERIFY_FAILED`. Any one of these fixes it:

- Install certifi for all users, which the plugin loads automatically (step 1.1 - see
  [certifi not found by the service](#certifi-not-found-by-the-service) below):

  ```bat
  py -3 -m pip install certifi
  ```

- Append the site's root certificate (PEM) to [`ca-bundle.pem`](../../ca-bundle.pem).
- Refresh the Windows root store from Windows Update, then import `roots.sst` into
  **Trusted Root Certification Authorities**:

  ```bat
  certutil -generateSSTFromWU roots.sst
  ```

The plugin logs which trust sources it loaded at startup - look for `TLS trust: loaded` in
`Logs\servermon.log`.

## certifi not found by the service

The Proxy Agent service runs the plugin as **LocalSystem**, not as you, so certifi must be
installed for all users - a copy in your own user profile is invisible to the service. If
`Logs\servermon.log` has no `TLS trust: loaded certifi bundle` line, the service cannot
see it.

If pip printed `Defaulting to user installation because normal site-packages is not
writeable`, it installed for you only: the Command Prompt was not elevated. Reinstall from
an **administrator** Command Prompt:

```bat
py -3 -m pip install certifi
```

To confirm certifi is visible outside your profile (`-s` makes Python ignore per-user
packages, which approximates what the service sees):

```bat
py -3 -s -c "import certifi; print(certifi.where())"
```

This should print a path in Python's own install folder (typically under
`C:\Program Files\`), not under your user folder. An `ImportError` means certifi is
installed for your user only.

---

[Previous: Part 5 - Pick one (~10 min)](05-pick-one.md) | [Lab index](README.md)
