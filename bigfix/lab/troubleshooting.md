[Previous: Part 5 - Pick one (~10 min)](05-pick-one.md) | [Lab index](README.md)

# Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| No devices ever appear | the plugin never ran | `Logs\servermon.log` under the plugin folder; the plugin path in `ExecutablePath` in `settings.json`; `py -3 --version` |
| Your config edit had no effect | you edited `settings.json`, not `servermon.toml` | `settings.json` is read by the service - restart `BESProxyAgent` |
| Nothing changes for half an hour | heartbeat still at the shipped value | `DeviceReportRefreshIntervalMinutes` in `settings.json`; or right-click the device and **Send Refresh** |
| Action status is `Error` | the plugin refused the argument | unknown field, bad value, or duplicate URL - `Logs\servermon.log` has the detail |
| Action never completes | the agent did not deliver the command | the `ProxyPluginCommands.json` whitelist - see [4.2](04-console.md#42-why-is-the-add-url-command-called-push-link) |
| A check fails with `CERTIFICATE_VERIFY_FAILED` | the site's root CA is in none of the trust sources | README -> [TLS trust store](../../README.md#tls-trust-store); append the PEM to `ca-bundle.pem` |
| Everything looks stuck and you want a clean slate | stale local state | README -> [Resetting the plugin's local state](../../README.md#resetting-the-plugins-local-state) |

---

[Previous: Part 5 - Pick one (~10 min)](05-pick-one.md) | [Lab index](README.md)
