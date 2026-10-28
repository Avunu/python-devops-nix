"""The Stack tab: devenv's processes as process-compose runs them, the
selected one's log, and starting, stopping, restarting and rebuilding.

The App polls the process table (DevTui.poll_stack) and calls stack_changed;
this tab polls the selected process's log itself, and only while it is shown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, RichLog, Static

from devtui import pc
from devtui.home import DOT, stack_line

if TYPE_CHECKING:
    from devtui.app import DevTui

COLUMNS = ("", "Process", "State", "Status", "Restarts", "PID")


class StackView(Vertical):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "restart", "Restart"),
        Binding("s", "start", "Start"),
        Binding("x", "stop", "Stop"),
        Binding("u", "app.stack_up", "Start stack"),
        Binding("b", "app.stack_rebuild", "Rebuild & restart"),
        Binding("d", "app.stack_down", "Stop stack"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._names: list[str] = []
        self._log_key: tuple[str, int, str] | None = None

    def compose(self) -> ComposeResult:
        app: DevTui = self.app  # type: ignore[assignment]
        yield Static("", id="stack-summary")
        with Horizontal(id="stack-main"):
            table: DataTable = DataTable(
                id="stack-table", cursor_type="row", zebra_stripes=True
            )
            yield table
            yield RichLog(
                id="stack-log", wrap=True, markup=False, highlight=False, max_lines=2000
            )
        with Horizontal(id="stack-actions"):
            yield Button("Start stack [u]", id="stack-up")
            yield Button("Rebuild & restart [b]", id="stack-rebuild", variant="primary")
            yield Button("Stop stack [d]", id="stack-down")
            for i, action in enumerate(
                app.spec.stack.actions if app.spec.stack else ()
            ):
                button = Button(action.label, id=f"stack-action-{i}")
                button.tooltip = action.help or " ".join(action.argv)
                yield button

    def on_mount(self) -> None:
        self.query_one("#stack-table", DataTable).add_columns(*COLUMNS)
        self.set_interval(1.0, self._poll_log)
        self.stack_changed()

    @property
    def selected(self) -> str | None:
        table = self.query_one("#stack-table", DataTable)
        if (
            not self._names
            or table.cursor_row < 0
            or table.cursor_row >= len(self._names)
        ):
            return None
        return self._names[table.cursor_row]

    def stack_changed(self) -> None:
        if not self.query("#stack-summary"):
            return  # a poll that landed before the tab's children were mounted
        app: DevTui = self.app  # type: ignore[assignment]
        processes = app.processes
        site = app.spec.site.process if app.spec.site else ""
        self.query_one("#stack-summary", Static).update(stack_line(processes, site))
        self.query_one("#stack-up", Button).display = processes is None
        self.query_one("#stack-down", Button).display = processes is not None
        table = self.query_one("#stack-table", DataTable)
        rows = processes or []
        names = [p.name for p in rows]
        if names != self._names:
            selected = self.selected
            table.clear()
            for p in rows:
                table.add_row(*self._cells(p), key=p.name)
            self._names = names
            if selected in names:
                table.move_cursor(row=names.index(selected))
        else:
            for p in rows:
                for column, cell in zip(table.columns, self._cells(p), strict=True):
                    table.update_cell(p.name, column, cell)

    @staticmethod
    def _cells(p: pc.Process) -> tuple[Text | str, ...]:
        colour = DOT.get(p.state, "")
        status = p.status
        if p.has_probe and p.running and p.ready:
            status = f"{status}, {p.ready.lower()}"
        if not p.running and p.status in ("Completed", "Error"):
            status = f"{status} ({p.exit_code})"
        return (
            Text("●", style=colour),
            p.name,
            Text(p.state, style=colour),
            status,
            str(p.restarts),
            str(p.pid or ""),
        )

    def _poll_log(self) -> None:
        if not self.display or self.app.screen is not self.screen:
            return
        name = self.selected
        if name is not None:
            self._fetch_log(name)

    @work(thread=True, exclusive=True, group="stack-log")
    def _fetch_log(self, name: str) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        try:
            lines = app.pc.logs(name)
        except pc.Error:
            return
        app.call_from_thread(self._show_log, name, lines)

    def _show_log(self, name: str, lines: list[str]) -> None:
        key = (name, len(lines), lines[-1] if lines else "")
        if key == self._log_key:
            return
        self._log_key = key
        log = self.query_one("#stack-log", RichLog)
        log.clear()
        log.border_title = name
        for line in lines:
            log.write(Text.from_ansi(line))

    @on(DataTable.RowHighlighted, "#stack-table")
    def _highlighted(self) -> None:
        self._log_key = None
        self._poll_log()

    def _process_call(self, verb: str) -> None:
        name = self.selected
        if name is None:
            return
        self._call(verb, name)

    @work(thread=True, group="stack-call")
    def _call(self, verb: str, name: str) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        try:
            getattr(app.pc, verb)(name)
        except pc.Error as exc:
            app.call_from_thread(app.notify, str(exc), severity="error")
            return
        app.call_from_thread(app.notify, f"{verb.capitalize()}: {name}")
        app.call_from_thread(app.poll_stack)

    def action_restart(self) -> None:
        self._process_call("restart")

    def action_start(self) -> None:
        self._process_call("start")

    def action_stop(self) -> None:
        self._process_call("stop")

    @on(Button.Pressed, "#stack-up")
    def _up(self) -> None:
        self.app.action_stack_up()  # type: ignore[attr-defined]

    @on(Button.Pressed, "#stack-rebuild")
    def _rebuild(self) -> None:
        self.app.action_stack_rebuild()  # type: ignore[attr-defined]

    @on(Button.Pressed, "#stack-down")
    def _down(self) -> None:
        self.app.action_stack_down()  # type: ignore[attr-defined]

    @on(Button.Pressed, "#stack-actions Button")
    def _action(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("stack-action-"):
            return
        app: DevTui = self.app  # type: ignore[assignment]
        assert app.spec.stack is not None
        app.run_spec_action(app.spec.stack.actions[int(button_id.rsplit("-", 1)[-1])])
