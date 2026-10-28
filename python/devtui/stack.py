"""Starting the stack detached, as it is or after re-evaluating the shell.

The stack's processes are fixed when `devenv up` evaluates them, so a change
that alters them (an OCA repository's addons path, a Frappe app in the uv
workspace) only takes effect from a fresh evaluation. Under devenv's flake
integration, `devenv up` is a wrapper (devenv's src/modules/flake-compat.nix)
that runs the shell's own process manager as it was loaded when the shell is a
direnv one ($DIRENV_DIR, or $DEVENV_IN_DIRENV_SHELL=true), and re-enters
`nix develop` first, re-evaluating, otherwise. So:

  - start: `devenv up -D` with the shell's environment;
  - rebuild: stop the project, then the same command with neither variable
    set, which re-evaluates the flake and starts what it now says.

Both export PC_TUI_ENABLED=0 (devenv's process-compose manager reads it): the
TUI is the interface, and -D leaves process-compose serving the socket in the
background, so the stack outlives the TUI. Standard library only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

UP = ("devenv", "up", "-D")

#: What makes devenv's flake wrapper use the loaded shell rather than
#: re-entering it.
LOADED_SHELL = (
    "DIRENV_DIR",
    "DIRENV_FILE",
    "DIRENV_DIFF",
    "DIRENV_WATCHES",
    "DEVENV_IN_DIRENV_SHELL",
)


def command(
    up: Sequence[str] = (), rebuild: Sequence[str] = (), *, fresh: bool
) -> list[str]:
    if fresh and rebuild:
        return list(rebuild)
    return list(up or UP)


def environment(
    base: Mapping[str, str] | None = None, *, fresh: bool
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["PC_TUI_ENABLED"] = "0"
    env["DEVENV_PROCESS_MANAGER_BACKGROUND"] = "1"
    if fresh:
        for name in LOADED_SHELL:
            env.pop(name, None)
    return env
