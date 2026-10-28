"""The TUI: four tabs (Home, Stack, Commands, Store), the command palette over
all of them, and the one place command lines are run from.

Tabs appear for what the spec has: Stack with "stack", Commands with
"commands", Store with a plugin whose catalog is not empty.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Footer, Header, TabbedContent, TabPane

from devtui import pc, stack
from devtui import spec as specmod
from devtui.commandsview import CommandsView
from devtui.home import HomeView
from devtui.plugin import Product, Step
from devtui.plugin import load as load_plugin
from devtui.run import ConfirmScreen, RunScreen, shell_line
from devtui.spec import Action, Spec
from devtui.stackview import StackView
from devtui.store import StoreView

#: The TUI's own actions, for a spec action's argv[0] (devtui.spec). Besides
#: these: devtui:restart (every running process), devtui:rebuild [COMMAND…]
#: (then COMMAND in the re-evaluated shell) and devtui:fresh COMMAND… (COMMAND
#: in a re-evaluated shell, the stack untouched).
INTERNAL = {
    "devtui:up": "stack_up",
    "devtui:down": "stack_down",
    "devtui:open": "open_site",
}

#: What each tab focuses when it is switched to.
FOCUS = {
    "home": "#home-actions Button",
    "stack": "#stack-table",
    "commands": "#cmd-tree",
    "store": "#store-table",
}

REBUILD = Action(label="Rebuild & restart the stack", argv=("devtui:rebuild",))
RESTART = Action(label="Restart the stack's processes", argv=("devtui:restart",))


class DevTui(App[int]):
    CSS_PATH = "devtui.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "tab('home')", "Home", show=False),
        Binding("2", "tab('stack')", "Stack", show=False),
        Binding("3", "tab('commands')", "Commands", show=False),
        Binding("4", "tab('store')", "Store", show=False),
        Binding("o", "open_site", "Open site"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        data: Any,
        variables: Mapping[str, str] | None = None,
        *,
        vars_command: str = "",
        client: pc.Client | None = None,
        product: Product | None = None,
    ) -> None:
        super().__init__()
        self.data = data
        self.static_vars = dict(variables or {})
        self.vars_command = vars_command
        self.spec: Spec = specmod.load(data, self._variables())
        self.title = self.spec.banner.title
        self.sub_title = self.spec.product
        self.pc = client or pc.Client(
            self.spec.stack.socket if self.spec.stack else None
        )
        self.product = product if product is not None else load_plugin(self.spec)
        #: The stack's processes, or None when process-compose is not running.
        self.processes: list[pc.Process] | None = None

    # -- spec ----------------------------------------------------------------------

    def _variables(self) -> dict[str, str]:
        out = {}
        if self.vars_command:
            try:
                result = subprocess.run(
                    [self.vars_command],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                out.update(specmod.parse_vars(result.stdout))
            except (OSError, subprocess.SubprocessError):
                pass
        out.update(self.static_vars)
        return out

    def refresh_spec(self) -> None:
        """Re-read the run-time values (a leased port) into the spec."""
        try:
            self.spec = specmod.load(self.data, self._variables())
        except specmod.SpecError as exc:
            self.notify(f"spec: {exc}", severity="error")
            return
        for view in self.query(HomeView):
            view.spec_changed()

    # -- layout --------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="home", id="tabs"):
            with TabPane("Home", id="home"):
                yield HomeView()
            if self.spec.stack is not None:
                with TabPane("Stack", id="stack"):
                    yield StackView()
            if self.spec.commands:
                with TabPane("Commands", id="commands"):
                    yield CommandsView()
            if self.product is not None and self.product.catalog().items:
                with TabPane("Store", id="store"):
                    yield StoreView(self.product)
        yield Footer()

    def on_mount(self) -> None:
        if self.spec.stack is not None:
            self.poll_stack()
            self.set_interval(1.0, self.poll_stack)

    def action_tab(self, tab: str) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        if not tabs.query(f"#{tab}"):
            return
        # Focus left in the old pane would bring it back (TabbedContent shows
        # the pane that holds the focus), so drop it first and then focus the
        # new tab's own widget: not an input, which would take the digits.
        self.set_focus(None)
        tabs.active = tab

        def focus() -> None:
            for widget in tabs.query(FOCUS.get(tab, "")):
                if widget.focusable:
                    widget.focus()
                    break

        self.call_after_refresh(focus)

    # -- the stack ---------------------------------------------------------------------

    def poll_stack(self) -> None:
        self._poll()

    @work(thread=True, exclusive=True, group="pc-poll")
    def _poll(self) -> None:
        try:
            processes: list[pc.Process] | None = self.pc.processes()
        except pc.Error:
            processes = None
        self.call_from_thread(self._stack_polled, processes)

    def _stack_polled(self, processes: list[pc.Process] | None) -> None:
        was_down = self.processes is None
        self.processes = processes
        if was_down and processes is not None:
            # Up again: a port leased at start may have moved.
            self.refresh_spec()
        for view in self.query(HomeView):
            view.stack_changed()
        for view in self.query(StackView):
            view.stack_changed()

    def _start(self, *, fresh: bool, then: Sequence[str] = ()) -> None:
        """Start the stack detached; `fresh`, from a re-evaluated shell. `then`
        runs after it, in a re-evaluated shell too (the TUI's own environment
        is the one it was started in)."""
        assert self.spec.stack is not None
        argv = stack.command(self.spec.stack.up, self.spec.stack.rebuild, fresh=fresh)
        title = "Rebuilding and starting the stack" if fresh else "Starting the stack"
        steps = [
            Step(
                tuple(argv),
                "re-evaluate the shell, then start it detached" if fresh else "",
            )
        ]
        if then:
            steps.append(
                Step((*self.spec.fresh_shell, *then), "in the re-evaluated shell")
            )
        self.run_steps(
            title,
            steps,
            env=stack.environment(fresh=fresh),
            on_done=lambda ok: (self.refresh_spec(), self.poll_stack()),
        )

    def action_stack_up(self) -> None:
        if self.spec.stack is None:
            return
        if self.processes is not None:
            self.notify("The stack is already running.")
            return
        self._start(fresh=False)

    def action_stack_rebuild(self, then: Sequence[str] = ()) -> None:
        if self.spec.stack is None:
            return
        if self.processes is None:
            self._start(fresh=True, then=then)
            return
        self.notify("Stopping the stack…")
        self._down_then(lambda: self._start(fresh=True, then=then))

    def action_stack_down(self) -> None:
        if self.spec.stack is not None and self.processes is not None:
            self.notify("Stopping the stack…")
            self._down_then(lambda: self.notify("The stack is stopped."))

    @work(thread=True, exclusive=True, group="pc-down")
    def _down_then(self, then: Callable[[], None]) -> None:
        try:
            self.pc.shutdown()
        except pc.Error as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")
            return
        deadline = time.monotonic() + 120
        while self.pc.alive() and time.monotonic() < deadline:
            time.sleep(0.5)
        if self.pc.alive():
            self.call_from_thread(
                self.notify,
                "process-compose is still up after 2 minutes.",
                severity="error",
            )
            return
        self.call_from_thread(self.poll_stack)
        self.call_from_thread(then)

    @work(thread=True, group="pc-restart")
    def _restart_all(self) -> None:
        try:
            for process in self.pc.processes():
                if process.running:
                    self.pc.restart(process.name)
        except pc.Error as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")
            return
        self.call_from_thread(self.notify, "Restarted the stack's processes.")

    def action_open_site(self) -> None:
        url = (
            self.spec.site.url
            if self.spec.site
            else next((u.url for u in self.spec.banner.urls), "")
        )
        if not url:
            self.notify("This project names no site.")
            return
        self.open_url(url)
        self.notify(f"Opening {url}")

    # -- running -------------------------------------------------------------------------

    @property
    def root(self) -> str | None:
        return self.spec.root or None

    def run_steps(
        self,
        title: str,
        steps: Sequence[Step],
        *,
        after: Sequence[Action] = (),
        env: Mapping[str, str] | None = None,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        def done(ok: bool | None) -> None:
            if on_done is not None:
                on_done(bool(ok))

        self.push_screen(
            RunScreen(
                title,
                steps,
                cwd=self.root,
                env=env,
                after=after,
                on_after=self.run_spec_action,
            ),
            done,
        )

    def run_interactive(self, argv: Sequence[str]) -> None:
        """Give `argv` the terminal; back to the TUI when it exits."""
        with self.suspend():
            print(f"\n$ {shell_line(argv)}\n", flush=True)
            try:
                rc = subprocess.run(list(argv), cwd=self.root, check=False).returncode
            except OSError as exc:
                print(f"{argv[0]}: {exc.strerror or exc}")
                rc = 127
            except KeyboardInterrupt:
                rc = 130
            try:
                input(f"\n[exited {rc}] Enter returns to the menu. ")
            except (EOFError, KeyboardInterrupt):
                pass

    def follow_ups(self, action: Action) -> list[Action]:
        return {"rebuild": [REBUILD], "restart": [RESTART]}.get(action.after, [])

    def run_spec_action(self, action: Action) -> None:
        head = action.argv[0]
        if head == "devtui:restart":
            self._restart_all()
            return
        if head == "devtui:rebuild":
            self.action_stack_rebuild(then=action.argv[1:])
            return
        if head == "devtui:fresh":
            argv = (*self.spec.fresh_shell, *action.argv[1:])
            self.run_steps(action.label, [Step(argv)], after=self.follow_ups(action))
            return
        if head in INTERNAL:
            getattr(self, f"action_{INTERNAL[head]}")()
            return
        if action.interactive:
            self.run_interactive(action.argv)
            return
        steps = [Step(action.argv)]
        if action.confirm:

            def confirmed(ok: bool | None) -> None:
                if ok:
                    self.run_steps(action.label, steps, after=self.follow_ups(action))

            self.push_screen(ConfirmScreen(f"{action.label}?", steps), confirmed)
        else:
            self.run_steps(action.label, steps, after=self.follow_ups(action))

    # -- the command palette -------------------------------------------------------------

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        for action in self.spec.actions:
            yield SystemCommand(
                action.label,
                action.help or shell_line(action.argv),
                lambda a=action: self.run_spec_action(a),
            )
        if self.spec.site is not None:
            yield SystemCommand(
                f"Open {self.spec.site.label}",
                self.spec.site.url,
                self.action_open_site,
            )
        if self.spec.stack is not None:
            yield SystemCommand(
                "Start the stack", "devenv up, detached", self.action_stack_up
            )
            yield SystemCommand(
                "Rebuild & restart the stack",
                "re-evaluate the shell, then start it",
                self.action_stack_rebuild,
            )
            yield SystemCommand(
                "Stop the stack",
                "every process, and process-compose",
                self.action_stack_down,
            )
            for action in self.spec.stack.actions:
                yield SystemCommand(
                    action.label,
                    action.help or shell_line(action.argv),
                    lambda a=action: self.run_spec_action(a),
                )
        for view in self.query(CommandsView):
            for root, command in view.all_commands():
                line = shell_line([root.name, *command.path])
                yield SystemCommand(
                    line,
                    command.summary or " ",
                    lambda r=root, c=command: self._show_command(r, c),
                )

    def _show_command(self, root: Any, command: Any) -> None:
        self.action_tab("commands")
        self.query_one(CommandsView).select(root, command)
