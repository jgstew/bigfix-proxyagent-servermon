[Previous: Part 4 - Edit servermon.toml directly (advanced, ~10 min)](04-edit-toml.md) | [Lab index](README.md) | [Next: Troubleshooting](troubleshooting.md)

# Part 5 - Pick one (~10 min)

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
- **Try the dashboard.** [`bigfix/content/dashboard-servermon.ojo`](../content/dashboard-servermon.ojo).
- **Discuss.** What in your own environment would you monitor this way, and how often?
  What would you *not* monitor this way?

---

[Previous: Part 4 - Edit servermon.toml directly (advanced, ~10 min)](04-edit-toml.md) | [Lab index](README.md) | [Next: Troubleshooting](troubleshooting.md)
