"""Puts the test support package and both stub products on ``sys.path``.

The stubs are installable directories (DESIGN §11.1); the core's own suite imports them
from the source tree, so it runs the same from a checkout and from the Nix check.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in (HERE, HERE / "stubs" / "stub", HERE / "stubs" / "kit"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
