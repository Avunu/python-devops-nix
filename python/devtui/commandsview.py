"""The Commands tab: every command the spec's catalogs list, as a tree; the
selected one's help, a form for its parameters, and the command line the form
makes, which Run runs (or hands the terminal to, for an interactive one).

A catalog from a file loads at once; one from a probe loads in a worker, and
shows as "loading…" until it has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Select, Static, Switch, Tree
from textual.widgets.tree import TreeNode

from devtui import commands
from devtui.commands import Command, Param
from devtui.plugin import Step
from devtui.run import shell_line

if TYPE_CHECKING:
    from devtui.app import DevTui


class CommandsView(Horizontal):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "focus_tree", "Back to the tree", show=False),
        Binding("slash", "filter", "Filter", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        #: Each loaded catalog's root, in the spec's order (None while loading).
        self.roots: list[Command | None] = []
        self.current: tuple[Command, Command] | None = None  # (root, command)
        self._widgets: list[tuple[Param, Widget]] = []
        self._generation = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="cmd-left"):
            yield Input(placeholder="Filter commands…", id="cmd-filter")
            tree: Tree[Any] = Tree("commands", id="cmd-tree")
            tree.show_root = False
            yield tree
        with VerticalScroll(id="cmd-right"):
            yield Static("", id="cmd-title")
            yield Static("", id="cmd-help")
            yield Vertical(id="cmd-form")
            yield Static("", id="cmd-preview", classes="preview")
            with Horizontal(id="cmd-buttons"):
                yield Button("Run", variant="primary", id="cmd-run", disabled=True)
                yield Button("--help", id="cmd-helpbtn", disabled=True)
                yield Button("Copy", id="cmd-copy", disabled=True)

    def on_mount(self) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        self.roots = [None] * len(app.spec.commands)
        for i, source in enumerate(app.spec.commands):
            if source.file:
                try:
                    self.roots[i] = commands.load_file(source.file)
                except (OSError, ValueError, KeyError) as exc:
                    app.notify(f"{source.file}: {exc}", severity="error")
            else:
                self._probe(i)
        self._fill()

    @work(thread=True, group="cmd-probe")
    def _probe(self, index: int) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        source = app.spec.commands[index]
        try:
            root = commands.run_probe(
                source.probe, source.cache, cwd=source.cwd or app.spec.root
            )
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            app.call_from_thread(
                app.notify,
                f"Could not list the commands: {exc}",
                severity="warning",
                timeout=10,
            )
            return
        app.call_from_thread(self._loaded, index, root)

    def _loaded(self, index: int, root: Command) -> None:
        self.roots[index] = root
        self._fill()

    def all_commands(self) -> list[tuple[Command, Command]]:
        """(root, command) for every leaf, for the command palette."""
        out = []
        for root in self.roots:
            if root is None:
                continue
            for command in root.walk():
                if not command.commands and command is not root:
                    out.append((root, command))
        return out

    def _fill(self) -> None:
        query = self.query_one("#cmd-filter", Input).value.strip().lower()
        tree = self.query_one("#cmd-tree", Tree)
        tree.clear()
        for root in self.roots:
            if root is None:
                tree.root.add_leaf(Text("loading…", style="dim"))
                continue
            label = Text.assemble(
                (root.name, "bold"), (f"  {root.source}" if root.source else "", "dim")
            )
            node = tree.root.add(label, data=(root, root), expand=True)
            self._add(node, root, root, query)

    def _add(
        self, node: TreeNode[Any], root: Command, command: Command, query: str
    ) -> bool:
        """Add `command`'s children under `node`; whether any matched."""
        any_match = False
        for child in command.commands:
            words = " ".join((root.name, *child.path, child.summary)).lower()
            if child.commands:
                sub = node.add(child.name, data=(root, child), expand=bool(query))
                matched = self._add(sub, root, child, query)
                if not matched and query and query not in words:
                    sub.remove()
                else:
                    any_match = True
            elif not query or all(term in words for term in query.split()):
                label = Text(child.name)
                if child.summary:
                    label.append(f"  {child.summary}", style="dim")
                node.add_leaf(label, data=(root, child))
                any_match = True
        return any_match

    def action_focus_tree(self) -> None:
        self.query_one("#cmd-tree", Tree).focus()

    def action_filter(self) -> None:
        self.query_one("#cmd-filter", Input).focus()

    @on(Input.Changed, "#cmd-filter")
    def _filter(self) -> None:
        self._fill()

    @on(Tree.NodeHighlighted, "#cmd-tree")
    def _highlighted(self, event: Tree.NodeHighlighted[Any]) -> None:
        if event.node.data is not None:
            self.show(*event.node.data)

    def show(self, root: Command, command: Command) -> None:
        self.current = (root, command)
        self._generation += 1
        title = Text(shell_line([root.name, *command.path]), style="bold")
        if command.interactive:
            title.append("   interactive: takes the terminal", style="yellow")
        self.query_one("#cmd-title", Static).update(title)
        self.query_one("#cmd-help", Static).update(
            Text(command.help or command.summary or "")
        )
        form = self.query_one("#cmd-form", Vertical)
        form.remove_children()
        self._widgets = []
        leaf = not command.commands
        if leaf:
            widgets: list[Widget] = []
            if command.raw:
                param = Param(
                    name="args",
                    kind="argument",
                    multiple=True,
                    help="arguments, as on the command line",
                )
                widgets += self._field(param, len(widgets))
            else:
                for param in command.params:
                    widgets += self._field(param, len(widgets))
            form.mount_all(widgets)
        for button_id in ("#cmd-run", "#cmd-copy"):
            self.query_one(button_id, Button).disabled = not leaf
        self.query_one("#cmd-helpbtn", Button).disabled = False
        self._preview()

    def _field(self, param: Param, index: int) -> list[Widget]:
        wid = f"cmd-p{self._generation}-{index}"
        text = Text(param.label, style="bold")
        if param.required:
            text.append(" *", style="red")
        if param.help:
            text.append(f"  {param.help}", style="dim")
        widget: Widget
        if param.flag:
            widget = Switch(value=param.default.lower() in ("true", "1"), id=wid)
        elif param.type == "choice" and param.choices:
            options = [(c, c) for c in param.choices]
            value = param.default if param.default in param.choices else Select.NULL
            widget = Select(options, value=value, allow_blank=True, id=wid)
        else:
            placeholder = param.default or (
                "several, space-separated" if param.multiple else param.type
            )
            widget = Input(placeholder=placeholder, id=wid)
        self._widgets.append((param, widget))
        return [Label(text), widget]

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for param, widget in self._widgets:
            if isinstance(widget, Switch):
                out[param.name] = widget.value
            elif isinstance(widget, Select):
                out[param.name] = None if widget.value is Select.NULL else widget.value
            elif isinstance(widget, Input):
                out[param.name] = widget.value
        return out

    def command_line(self) -> list[str] | None:
        if self.current is None:
            return None
        root, command = self.current
        return commands.argv(root.name, command, self.values())

    @on(Input.Changed, "#cmd-form Input")
    @on(Switch.Changed)
    @on(Select.Changed)
    def _preview(self) -> None:
        line = self.command_line()
        preview = self.query_one("#cmd-preview", Static)
        if line is None:
            preview.update("")
            return
        text = Text(f"$ {shell_line(line)}", style="bold")
        if self.current is not None:
            gaps = commands.missing(self.current[1], self.values())
            if gaps:
                text.append(f"\n  needs: {', '.join(gaps)}", style="yellow")
        preview.update(text)

    @on(Button.Pressed, "#cmd-run")
    def run(self) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        line = self.command_line()
        if line is None or self.current is None:
            return
        command = self.current[1]
        gaps = commands.missing(command, self.values())
        if gaps:
            app.notify(f"Needed: {', '.join(gaps)}", severity="warning")
            return
        if command.interactive:
            app.run_interactive(line)
        else:
            app.run_steps(shell_line(line), [Step(tuple(line))])

    @on(Button.Pressed, "#cmd-helpbtn")
    def _help(self) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        if self.current is None:
            return
        root, command = self.current
        line = [root.name, *command.path, "--help"]
        app.run_steps(shell_line(line), [Step(tuple(line))])

    @on(Button.Pressed, "#cmd-copy")
    def _copy(self) -> None:
        line = self.command_line()
        if line is not None:
            self.app.copy_to_clipboard(shell_line(line))
            self.app.notify("Copied the command line.")

    def select(self, root: Command, command: Command) -> None:
        """Show `command`, from the palette: highlight its node."""
        tree = self.query_one("#cmd-tree", Tree)

        def find(node: TreeNode[Any]) -> TreeNode[Any] | None:
            if node.data == (root, command):
                return node
            for child in node.children:
                hit = find(child)
                if hit is not None:
                    return hit
            return None

        node = find(tree.root)
        if node is not None:
            tree.select_node(node)
            tree.move_cursor(node)
        self.show(root, command)
