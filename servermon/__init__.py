"""BigFix Management Extender (Proxy Agent) plugin that monitors web server URLs.

Each monitored URL appears in BigFix as a proxied device whose last report
time is the last time the URL was checked. Built on the bigfix-proxyagent SDK
(https://github.com/jgstew/bigfix-proxyagent); protocol reference in
bigfix/reference-files/ProxyAgents.md.
"""

# Make the bigfix-proxyagent SDK importable before any servermon submodule
# needs it. Prefer an installed copy (a relay may pip install it); otherwise
# fall back to the wheel vendored in vendor/ so the plugin still runs straight
# from a copied folder with no pip install. Kept dependency-free and inline so
# it runs on "import servermon" before submodules import bigfix_proxyagent.
import glob as _glob
import os as _os
import sys as _sys


def _ensure_sdk() -> None:
    vendor = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "vendor"
    )
    try:
        import bigfix_proxyagent  # noqa: F401
    except ImportError:
        wheels = sorted(
            _glob.glob(_os.path.join(vendor, "bigfix_proxyagent-*.whl"))
        )
        if wheels and wheels[-1] not in _sys.path:
            _sys.path.insert(0, wheels[-1])
    # Register vendor/ so the SDK prefers a loose wheel this plugin ships there
    # (e.g. a pinned tomlkit) over its bundled copy - the default for all SDK
    # dependency loads, including the SDK's own config editor. Best-effort: a
    # bootstrap hiccup must never break "import servermon".
    try:
        from bigfix_proxyagent import vendor as _vendor

        _vendor.set_plugin_vendor_dir(vendor)
    except ImportError:
        pass


_ensure_sdk()

__version__ = "4.1.2"
