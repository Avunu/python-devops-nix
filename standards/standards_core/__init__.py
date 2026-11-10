"""standards-core: the engine product plugins build their standards tools on (DESIGN.md)."""

import json
import re
from importlib import metadata
from importlib.resources import files

# x-release-please-start-version
__version__ = "1.5.0"
# x-release-please-end

_SHA = re.compile(r"[0-9a-f]{40}")


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def revision() -> str | None:
    """The python-devops-nix commit this copy of the core was built from, or ``None``.

    A ``git+https://…@<sha>`` install records the commit in ``direct_url.json``; the Nix
    build writes ``_source.json`` beside this file. A path or editable install has
    neither, so it has no revision and the skew row that compares it is skipped
    (DESIGN §9.4).
    """
    try:
        text = metadata.distribution("standards-core").read_text("direct_url.json")
        rev = _sha(json.loads(text or "null")["vcs_info"]["commit_id"])
    except (metadata.PackageNotFoundError, ValueError, KeyError, TypeError):
        rev = None
    if rev:
        return rev
    try:
        return _sha(
            json.loads((files("standards_core") / "_source.json").read_text())["rev"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
