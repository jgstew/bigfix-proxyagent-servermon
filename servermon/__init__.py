"""BigFix Management Extender (Proxy Agent) plugin that monitors web server URLs.

Each monitored URL appears in BigFix as a proxied device whose last report
time is the last time the URL was checked. Plugin protocol modeled on
https://github.com/bigfix/trask
"""

__version__ = "3.1.3"
