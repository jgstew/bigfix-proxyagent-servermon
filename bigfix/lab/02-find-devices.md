[Previous: Part 1 - Install the plugin (~10 min)](01-install.md) | [Lab index](README.md) | [Next: Part 3 - Add URLs to monitor (~10 min)](03-add-urls.md)

# Part 2 - Find the devices in the console (~5 min)

## 2.1 Find the devices in the BigFix console

Within one heartbeat, the URLs from the shipped [`servermon.toml`](../../servermon.toml) appear in **Computers**
as ordinary-looking devices:

- **Device Type** is `Web Server`.
- The **OS** column shows the web server's `Server` header (for example `nginx/1.25.3`).
- The device name is the URL with the scheme removed - `https://example.com` becomes
  `example.com`.

## 2.2 Import the analysis

Import [`bigfix/content/analysis-servermon.bes`](../content/analysis-servermon.bes) into the console and **activate** it. It
exposes everything the plugin reports as properties: response code, check result,
response time, TLS version, certificate expiry and more.

> **Checkpoint 1** - you have a device named `example.com` whose *HTTP Response Code* is
> `200` and whose *HTTP Check Result* starts with `OK:`.

**What to notice**

- One `[[urls]]` entry in [`servermon.toml`](../../servermon.toml) equals exactly one device in BigFix.
- Device identity is the **full URL**, so `http://example.com` and `https://example.com`
  are two separate devices with separate history.
- **Last Report Time** is the last time the URL actually *answered*. A URL that stops
  responding goes stale and eventually greys out like a dead client, while its other
  properties keep updating. That is deliberate - it makes a dead site look dead.

---

[Previous: Part 1 - Install the plugin (~10 min)](01-install.md) | [Lab index](README.md) | [Next: Part 3 - Add URLs to monitor (~10 min)](03-add-urls.md)
