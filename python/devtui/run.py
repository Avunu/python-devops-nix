"""Running command lines for the user: the confirmation, the streamed log, and
the terminal handed over to an interactive one.

A step's output goes to a file the screen tails, not to a pipe: `devenv up -D`
leaves process-compose running in the background holding whatever it was given
as stdout, and a pipe would then never close. The step is done when its own
process exits. Each runs in a session of its own, so cancelling stops what it
started with it, and stdin is /dev/null, so the product's command line takes
its non-interactive path (no prompt can wait for an answer nobody sees).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog, Static

from devtui.plugin import Form, Step
from devtui.spec import Action


def shell_line(argv: Sequence[str]) -> str:
    return shlex.join(argv)


class ConfirmScreen(ModalScreen[bool]):
    """What is about to run, before it does: Run, Copy or Cancel."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(
        self, title: str, steps: Sequence[Step], notes: Sequence[str] = ()
    ) -> None:
        super().__init__()
        self.title_text = title
        self.steps = list(steps)
        self.notes = list(notes)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            if self.notes:
                yield Static(Text("\n".join(self.notes)), classes="dialog-notes")
            commands = Text()
            for i, step in enumerate(self.steps):
                if i:
                    commands.append("\n")
                if step.label:
                    commands.append(f"# {step.label}\n", style="dim")
                commands.append(f"$ {shell_line(step.argv)}", style="bold")
            yield VerticalScroll(Static(commands), classes="dialog-commands")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Run", variant="primary", id="confirm-run")
                yield Button("Copy", id="confirm-copy")
                yield Button("Cancel", id="confirm-cancel")

    def on_mount(self) -> None:
        self.query_one("#confirm-run", Button).focus()

    def action_copy(self) -> None:
        self.app.copy_to_clipboard("\n".join(shell_line(s.argv) for s in self.steps))
        self.notify("Copied the command line.")

    @on(Button.Pressed, "#confirm-run")
    def _run(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-copy")
    def _copy(self) -> None:
        self.action_copy()

    @on(Button.Pressed, "#confirm-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)


class FormScreen(ModalScreen["dict[str, str] | None"]):
    """A plugin's Form: its fields, and the command line they make."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss(None)", "Cancel")
    ]

    def __init__(self, form: Form) -> None:
        super().__init__()
        self.form = form

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.form.label, classes="dialog-title")
            if self.form.help:
                yield Static(Text(self.form.help), classes="dialog-notes")
            for field in self.form.fields:
                yield Label(field.label + (" *" if field.required else ""))
                yield Input(placeholder=field.placeholder, id=f"form-{field.name}")
            yield Static("", id="form-preview", classes="preview")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Continue", variant="primary", id="form-ok")
                yield Button("Cancel", id="form-cancel")

    def values(self) -> dict[str, str]:
        return {
            f.name: self.query_one(f"#form-{f.name}", Input).value
            for f in self.form.fields
        }

    def on_mount(self) -> None:
        self._preview()

    @on(Input.Changed)
    def _preview(self) -> None:
        self.query_one("#form-preview", Static).update(
            Text("$ " + shell_line(self.form.build(self.values())), style="bold")
        )

    @on(Input.Submitted)
    @on(Button.Pressed, "#form-ok")
    def _ok(self) -> None:
        values = self.values()
        missing = [
            f.label
            for f in self.form.fields
            if f.required and not values[f.name].strip()
        ]
        if missing:
            self.notify(f"Needed: {', '.join(missing)}", severity="warning")
            return
        self.dismiss(values)

    @on(Button.Pressed, "#form-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class RunScreen(ModalScreen[bool]):
    """Each step in turn, its output streamed; stops at the first that fails.
    Once all succeed, the `after` actions are offered as buttons."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        title: str,
        steps: Sequence[Step],
        *,
        cwd: str | None,
        env: Mapping[str, str] | None = None,
        after: Sequence[Action] = (),
        on_after: Callable[[Action], None] | None = None,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.steps = list(steps)
        self.cwd = cwd or None
        self.env = dict(env) if env is not None else None
        self.after = list(after)
        self.on_after = on_after
        self.proc: asyncio.subprocess.Process | None = None
        self.ok: bool | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(self.title_text, classes="dialog-title")
            yield RichLog(id="run-log", wrap=True, markup=False, highlight=False)
            with Horizontal(classes="dialog-buttons"):
                yield Static("running…", id="run-status")
                for i, action in enumerate(self.after):
                    yield Button(
                        action.label,
                        id=f"run-after-{i}",
                        classes="run-after",
                        disabled=True,
                    )
                yield Button("Close", id="run-close", disabled=True)

    def on_mount(self) -> None:
        self.run_worker(self._run(), exclusive=True, group="run")

    async def _run(self) -> None:
        log = self.query_one("#run-log", RichLog)
        rc = 0
        for step in self.steps:
            if step.label:
                log.write(Text(f"# {step.label}", style="dim"))
            log.write(Text(f"$ {shell_line(step.argv)}", style="bold cyan"))
            rc = await self._one(step.argv, log)
            if rc != 0:
                break
        self.ok = rc == 0
        status = self.query_one("#run-status", Static)
        if self.ok:
            status.update(Text("✓ done", style="bold green"))
            for button in self.query(".run-after").results(Button):
                button.disabled = False
        else:
            status.update(Text(f"✗ exited {rc}", style="bold red"))
        close = self.query_one("#run-close", Button)
        close.disabled = False
        buttons = list(self.query(".run-after").results(Button))
        (buttons[0] if self.ok and buttons else close).focus()

    async def _one(self, argv: Sequence[str], log: RichLog) -> int:
        env = dict(os.environ if self.env is None else self.env)
        env.setdefault("COLUMNS", str(max(40, log.size.width - 2)))
        with tempfile.TemporaryFile() as out:
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=self.cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                log.write(Text(f"{argv[0]}: {exc.strerror or exc}", style="red"))
                return 127
            partial = b""
            offset = 0
            while True:
                done = self.proc.returncode is not None
                out.seek(offset)
                chunk = out.read()
                offset += len(chunk)
                if chunk:
                    partial = self._write(log, partial + chunk)
                if done:
                    break
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=0.1)
                except TimeoutError:
                    pass
            if partial:
                self._write(log, partial + b"\n")
            return self.proc.returncode or 0

    @staticmethod
    def _write(log: RichLog, data: bytes) -> bytes:
        *lines, rest = data.split(b"\n")
        for line in lines:
            # A progress bar redraws its line with \r: keep what it ended on.
            text = line.decode(errors="replace").rstrip("\r").rsplit("\r", 1)[-1]
            log.write(Text.from_ansi(text))
        return rest

    def action_cancel(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            self.query_one("#run-log", RichLog).write(Text("cancelled", style="yellow"))

    def action_close(self) -> None:
        if self.ok is None:
            self.notify("Still running: ctrl+c cancels it.", severity="warning")
            return
        self.dismiss(bool(self.ok))

    @on(Button.Pressed, "#run-close")
    def _close(self) -> None:
        self.action_close()

    @on(Button.Pressed, ".run-after")
    def _after(self, event: Button.Pressed) -> None:
        index = int((event.button.id or "").rsplit("-", 1)[-1])
        self.dismiss(bool(self.ok))
        if self.on_after is not None:
            self.on_after(self.after[index])
