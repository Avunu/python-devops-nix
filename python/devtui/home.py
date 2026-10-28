"""The Home tab: the shell's banner, what the stack is doing, and the
product's quick actions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import banner
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Static

from devtui import pc

if TYPE_CHECKING:
    from devtui.app import DevTui

DOT = {
    "up": "green",
    "starting": "yellow",
    "done": "green",
    "failed": "red",
    "down": "red",
}


def stack_line(processes: Sequence[pc.Process] | None, site_process: str = "") -> Text:
    if processes is None:
        return Text.assemble(
            ("● ", "red"), "The stack is not running. ", ("u", "bold"), " starts it."
        )
    if not processes:
        return Text.assemble(
            ("● ", "yellow"), "process-compose is up with no processes."
        )
    states = [p.state for p in processes]
    up = sum(s in ("up", "done") for s in states)
    failed = [p.name for p in processes if p.state == "failed"]
    colour = "red" if failed else "green" if up == len(processes) else "yellow"
    line = Text.assemble(("● ", colour), f"{up}/{len(processes)} processes up")
    if failed:
        line.append(f" — failed: {', '.join(failed)}", style="red")
    site = next((p for p in processes if p.name == site_process), None)
    if site is not None:
        line.append(f" · {site_process} is {site.state}", style=DOT.get(site.state, ""))
    return line


class HomeView(VerticalScroll):
    def compose(self) -> ComposeResult:
        app: DevTui = self.app  # type: ignore[assignment]
        yield Static(banner.renderable(app.spec.banner), id="home-banner")
        yield Static("", id="home-stack")
        if app.spec.site is not None:
            yield Static(
                Text.assemble(
                    (f"{app.spec.site.label}: ", "bold"),
                    (app.spec.site.url, f"link {app.spec.site.url}"),
                    "   ",
                    ("o", "bold"),
                    " opens it in the browser",
                ),
                id="home-site",
            )
        with Horizontal(id="home-actions"):
            for i, action in enumerate(app.spec.actions):
                label = f"{action.label} [{action.key}]" if action.key else action.label
                button = Button(label, id=f"home-action-{i}")
                button.tooltip = action.help or " ".join(action.argv)
                yield button
        yield Static(
            Text(
                "1 Home · 2 Stack · 3 Commands · 4 Store · ctrl+p every command · q quit",
                style="dim",
            ),
            id="home-keys",
        )

    def spec_changed(self) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        self.query_one("#home-banner", Static).update(
            banner.renderable(app.spec.banner)
        )
        self.stack_changed()

    def stack_changed(self) -> None:
        if not self.query("#home-stack"):
            return
        app: DevTui = self.app  # type: ignore[assignment]
        if app.spec.stack is None:
            self.query_one("#home-stack", Static).display = False
            return
        site = app.spec.site.process if app.spec.site else ""
        self.query_one("#home-stack", Static).update(stack_line(app.processes, site))

    @on(Button.Pressed, "#home-actions Button")
    def _action(self, event: Button.Pressed) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        index = int((event.button.id or "").rsplit("-", 1)[-1])
        app.run_spec_action(app.spec.actions[index])
