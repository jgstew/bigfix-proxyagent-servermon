#!/usr/bin/env python3
"""Proxy Agent entry point.

settings.json points the Proxy Agent at this file; it runs the servermon
package straight from the repository checkout, no pip install needed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from servermon.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
