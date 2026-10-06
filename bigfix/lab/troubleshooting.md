[Previous: Part 5 - Pick one (~10 min)](05-pick-one.md) | [Lab index](README.md)

# Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| No devices ever appear | the plugin never ran | `Logs\servermon.log` under the plugin folder; the plugin path in `ExecutablePath` in [`settings.json`](../../settings.json); `py -3 --version` |
| Your config edit had no effect | you edited [`settings.json`](../../settings.json), not [`servermon.toml`](../../servermon.toml) | [`settings.json`](../../settings.json) is read by the service - restart `BESProxyAgent` |
| Nothing changes for half an hour | heartbeat still at the shipped value | `DeviceReportRefreshIntervalMinutes` in [`settings.json`](../../settings.json); or right-click the device and **Send Refresh** |
| Action status is `Error` | the plugin refused the argument | unknown field, bad value, or duplicate URL - `Logs\servermon.log` has the detail |
| Action never completes | the agent did not deliver the command | the `ProxyPluginCommands.json` whitelist - see [3.2](03-console.md#32-why-is-the-add-url-command-called-push-link) |
| A check fails with `CERTIFICATE_VERIFY_FAILED` | the site's root CA is in none of the trust sources | README -> [TLS trust store](../../README.md#tls-trust-store); append the PEM to [`ca-bundle.pem`](../../ca-bundle.pem) |
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

---

[Previous: Part 5 - Pick one (~10 min)](05-pick-one.md) | [Lab index](README.md)
