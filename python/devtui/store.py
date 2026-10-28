"""The Store tab: the plugin's catalog, searched and filtered; what is picked
to add or to remove; what that brings with it; and the command lines that do
it, confirmed, then run.

space picks the highlighted row (an installed one is picked for removal),
`b` picks the bundle shown, `a` applies the picks, and `/` goes to the search.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from devtui import catalog as cat
from devtui.plugin import Closure, Product
from devtui.run import ConfirmScreen, FormScreen

if TYPE_CHECKING:
    from devtui.app import DevTui

#: Rows shown at once; a narrower search shows the rest.
LIMIT = 400

COLUMNS = ("mark", "name", "summary", "group")

#: The filter list's width, less its padding and scrollbar.
FILTER_WIDTH = 22


def _line(name: str, count: int, prefix: tuple[str, str] = ("", "")) -> Text:
    """One line of the filter list, cut short rather than wrapped (the list
    wraps whatever it is given)."""
    tail = f" ({count})"
    room = FILTER_WIDTH - len(prefix[0]) - len(tail)
    if len(name) > room:
        name = name[: room - 1] + "…"
    return Text.assemble(prefix, name, (tail, "dim"))


class StoreView(Vertical):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("space", "toggle", "Pick"),
        Binding("b", "toggle_bundle", "Pick bundle"),
        Binding("a", "apply", "Apply picks"),
        Binding("slash", "search", "Search", show=False),
        Binding("escape", "focus_table", "Back to the list", show=False),
    ]

    def __init__(self, product: Product) -> None:
        super().__init__()
        self.product = product
        self.catalog = product.catalog()
        self.installed = product.installed()
        self.adds: list[str] = []
        self.removes: list[str] = []
        self.bundles: list[str] = []
        self.filter = ""  # "", "bundle:<id>", "group:<name>", "installed", "picked"
        self.rows: list[cat.Item] = []
        self._detail_for = ""
        self._search_timer = None
        self._widths: dict[str, int] = {}
        #: The detail pane's latest text, and the lock its updates take turns
        #: on: two Markdown updates at once can leave it empty.
        self._markdown = ""
        self._markdown_lock = asyncio.Lock()

    # -- layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        c = self.catalog
        with Horizontal(id="store-top"):
            yield Input(
                placeholder=f"Search {c.noun}s: a name, a {c.group_label.lower()}, a word…",
                id="store-search",
            )
            categories = c.categories()
            if categories:
                yield Select(
                    [(f"{name} ({n})", name) for name, n in categories],
                    prompt="Any category",
                    id="store-category",
                )
        with Horizontal(id="store-main"):
            yield OptionList(*self._filters(), id="store-filters")
            table: DataTable = DataTable(
                id="store-table", cursor_type="row", zebra_stripes=True
            )
            yield table
            with VerticalScroll(id="store-side"):
                yield Markdown("", id="store-detail")
        with Horizontal(id="store-cart"):
            yield Static("", id="store-cart-text")
            yield Button(
                "Apply [a]", variant="primary", id="store-apply", disabled=True
            )
            yield Button("Clear", id="store-clear", disabled=True)
            for i, form in enumerate(self.product.forms()):
                yield Button(form.label, id=f"store-form-{i}")

    def _filters(self) -> list[Option | None]:
        c = self.catalog
        options: list[Option | None] = [
            Option(f"All {c.noun}s ({len(c.items)})", id="all"),
            Option("Installed", id="installed"),
            Option("Picked", id="picked"),
        ]
        if c.bundles:
            options.append(None)
            for b in c.bundles:
                options.append(
                    Option(
                        _line(b.id, len(b.members), ("◆ ", "magenta")),
                        id=f"bundle:{b.id}",
                    )
                )
        groups = c.groups()
        if groups:
            options.append(None)
            for name, n in groups:
                options.append(Option(_line(name, n), id=f"group:{name}"))
        return options

    def on_mount(self) -> None:
        self._columns()
        self.refresh_rows()
        self._cart()

    def on_resize(self) -> None:
        self.call_after_refresh(self._relayout)

    def _relayout(self) -> None:
        if self._columns():
            self.refresh_rows()

    def _columns(self) -> bool:
        """The table's columns, sized to its width: the summary takes what the
        name and the group leave, so nothing scrolls sideways. Whether they
        changed."""
        table = self.query_one("#store-table", DataTable)
        # The vertical scrollbar, and a cell's padding either side.
        usable = max(40, table.size.width - 2 - 2 * len(COLUMNS))
        items = self.catalog.items
        name = min(max((len(i.id) + 2 for i in items), default=10), 34, usable // 3)
        group = min(max((len(i.group) for i in items), default=0), 20, usable // 5)
        widths = {"mark": 1, "name": name, "group": group}
        widths["summary"] = max(10, usable - sum(widths.values()))
        if widths == self._widths:
            return False
        self._widths = widths
        table.clear(columns=True)
        table.add_column("", key="mark", width=widths["mark"])
        table.add_column(self.catalog.noun.capitalize(), key="name", width=name)
        table.add_column("Summary", key="summary", width=widths["summary"])
        table.add_column(self.catalog.group_label, key="group", width=group)
        return True

    # -- rows --------------------------------------------------------------------

    def _query(self) -> tuple[str, str]:
        query = self.query_one("#store-search", Input).value
        category = ""
        if self.catalog.categories():
            value = self.query_one("#store-category", Select).value
            category = "" if value is Select.NULL else str(value)
        return query, category

    def refresh_rows(self, *, detail: bool = True) -> None:
        query, category = self._query()
        only: list[str] | None = None
        group = ""
        f = self.filter
        if f.startswith("bundle:"):
            bundle = next(b for b in self.catalog.bundles if b.id == f[7:])
            only = list(bundle.members)
        elif f.startswith("group:"):
            group = f[6:]
        elif f == "installed":
            only = sorted(self.installed)
        elif f == "picked":
            only = (
                self.adds
                + self.removes
                + [m for b in self._picked_bundles() for m in b.members]
            )
        hits = cat.search(
            self.catalog.items, query, group=group, category=category, only=only
        )
        self.rows = hits[:LIMIT]
        table = self.query_one("#store-table", DataTable)
        table.clear()
        for item in self.rows:
            table.add_row(*self._cells(item), key=item.id)
        more = len(hits) - len(self.rows)
        table.border_subtitle = f"{len(hits)} {self.catalog.noun}s" + (
            f", the first {LIMIT} shown" if more > 0 else ""
        )
        if self.rows and detail:
            self._detail(self.rows[0])

    def _bundle_members(self) -> set[str]:
        return {m for b in self._picked_bundles() for m in b.members}

    def _picked_bundles(self) -> list[cat.Bundle]:
        return [b for b in self.catalog.bundles if b.id in self.bundles]

    def _cells(self, item: cat.Item) -> tuple[Text, Text, Text, Text]:
        """mark: + picked, − picked for removal, ◆ in a picked bundle,
        ✓ installed; dim for what this version cannot install."""
        if item.id in self.adds:
            mark = Text("+", style="bold green")
        elif item.id in self.removes:
            mark = Text("−", style="bold red")
        elif item.id in self._bundle_members():
            mark = Text("◆", style="magenta")
        elif item.id in self.installed:
            mark = Text("✓", style="green")
        else:
            mark = Text("")
        style = "" if item.available else "dim"
        name = Text(item.id, style=style, no_wrap=True, overflow="ellipsis")
        if "application" in item.tags:
            name.append(" ★", style="yellow")
        summary = Text(
            item.summary or item.title or item.note,
            style=style,
            overflow="ellipsis",
            no_wrap=True,
        )
        group = Text(item.group, style="dim", no_wrap=True, overflow="ellipsis")
        return mark, name, summary, group

    def _redraw(self, item_id: str) -> None:
        item = self.catalog.by_id.get(item_id)
        table = self.query_one("#store-table", DataTable)
        if item is None or item_id not in table.rows:
            return
        for key, cell in zip(
            COLUMNS,
            self._cells(item),
            strict=True,
        ):
            table.update_cell(item_id, key, cell)

    # -- events --------------------------------------------------------------------

    @on(Input.Changed, "#store-search")
    def _search_changed(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.15, self.refresh_rows)

    @on(Input.Submitted, "#store-search")
    def _search_submitted(self) -> None:
        self.query_one("#store-table", DataTable).focus()

    @on(Select.Changed, "#store-category")
    def _category(self) -> None:
        self.refresh_rows()

    @on(OptionList.OptionHighlighted, "#store-filters")
    @on(OptionList.OptionSelected, "#store-filters")
    def _filter(
        self, event: OptionList.OptionHighlighted | OptionList.OptionSelected
    ) -> None:
        f = event.option.id or "all"
        f = "" if f == "all" else f
        if f != self.filter:
            self.filter = f
            search = self.query_one("#store-search", Input)
            if f.startswith("bundle:") and search.value:
                # A stack is a short list: show all of it, not what of it the
                # search left.
                with search.prevent(Input.Changed):
                    search.value = ""
            bundle = f.startswith("bundle:")
            self.refresh_rows(detail=not bundle)
            if bundle:
                self._bundle_detail(f[7:])

    @on(DataTable.RowHighlighted, "#store-table")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        item = self.catalog.by_id.get(str(event.row_key.value))
        if item is not None:
            self._detail(item)

    def _current(self) -> cat.Item | None:
        table = self.query_one("#store-table", DataTable)
        if not self.rows or not 0 <= table.cursor_row < len(self.rows):
            return None
        return self.rows[table.cursor_row]

    # -- detail ----------------------------------------------------------------------

    def _show(self, markdown: str) -> None:
        self._markdown = markdown
        self.run_worker(self._update_markdown(markdown), group="store-markdown")

    async def _update_markdown(self, markdown: str) -> None:
        async with self._markdown_lock:
            if markdown is self._markdown:
                await self.query_one("#store-detail", Markdown).update(markdown)

    def _facts(self, item: cat.Item) -> str:
        lines = [f"## {item.title or item.id}", ""]
        if item.summary and item.summary != item.title:
            lines += [item.summary, ""]
        rows = [(self.catalog.noun.capitalize(), f"`{item.id}`")]
        if item.group:
            rows.append((self.catalog.group_label, item.group))
        if item.version:
            rows.append(("Version", item.version))
        if item.license:
            rows.append(("License", item.license))
        if item.categories:
            rows.append(("Categories", ", ".join(item.categories)))
        if item.depends:
            rows.append(("Depends on", ", ".join(item.depends)))
        rows.extend(item.facts)
        if item.url:
            rows.append(("Link", f"<{item.url}>"))
        state = (
            "**installed**"
            if item.id in self.installed
            else ("available" if item.available else item.note or "unavailable")
        )
        rows.append(("State", state))
        lines += [f"- **{k}:** {v}" for k, v in rows]
        return "\n".join(lines)

    def _detail(self, item: cat.Item) -> None:
        if item.id == self._detail_for:
            return
        self._detail_for = item.id
        self._show(self._facts(item))
        self._more(item)

    @work(thread=True, exclusive=True, group="store-detail")
    def _more(self, item: cat.Item) -> None:
        try:
            extra = self.product.detail(item)
        except Exception as exc:  # noqa: BLE001 -- a plugin's network fetch is never fatal
            extra = f"_{exc}_"
        if extra:
            self.app.call_from_thread(self._show_more, item, extra)

    def _show_more(self, item: cat.Item, extra: str) -> None:
        if item.id == self._detail_for:
            self._show(self._facts(item) + "\n\n---\n\n" + extra)

    def _bundle_detail(self, bundle_id: str) -> None:
        bundle = next(b for b in self.catalog.bundles if b.id == bundle_id)
        self._detail_for = f"bundle:{bundle_id}"
        noun = self.catalog.bundle_noun
        body = [f"## {bundle.title or bundle.id}", ""]
        if bundle.summary:
            body += [bundle.summary, ""]
        body.append(
            f"{len(bundle.members)} {self.catalog.noun}s. **b** picks the whole {noun}."
        )
        missing = [
            m
            for m in bundle.members
            if m not in self.catalog.by_id or not self.catalog.by_id[m].available
        ]
        if missing:
            body += ["", f"Not available here, and skipped: {', '.join(missing)}"]
        self._show("\n".join(body))

    # -- picking -----------------------------------------------------------------------

    def action_focus_table(self) -> None:
        self.query_one("#store-table", DataTable).focus()

    def action_search(self) -> None:
        self.query_one("#store-search", Input).focus()

    def action_toggle(self) -> None:
        item = self._current()
        if item is None:
            return
        if item.id in self.installed:
            target = self.removes
        elif not item.available:
            self.notify(
                f"{item.id}: {item.note or 'not available here'}", severity="warning"
            )
            return
        else:
            target = self.adds
        if item.id in target:
            target.remove(item.id)
        else:
            target.append(item.id)
        self._redraw(item.id)
        self._cart()

    def action_toggle_bundle(self) -> None:
        if not self.filter.startswith("bundle:"):
            self.notify(f"Choose a {self.catalog.bundle_noun} on the left first.")
            return
        bundle_id = self.filter[7:]
        if bundle_id in self.bundles:
            self.bundles.remove(bundle_id)
        else:
            self.bundles.append(bundle_id)
        bundle = next(b for b in self.catalog.bundles if b.id == bundle_id)
        for member in bundle.members:
            self._redraw(member)
        self._cart()

    def closure(self) -> Closure:
        if not self.adds and not self.bundles:
            return Closure()
        return self.product.closure(self.adds, self.bundles)

    def _cart(self) -> None:
        parts = []
        if self.adds:
            parts.append(
                Text(f"+{len(self.adds)} {self.catalog.noun}(s)", style="green")
            )
        if self.bundles:
            parts.append(Text(f"◆ {', '.join(self.bundles)}", style="magenta"))
        if self.removes:
            parts.append(Text(f"−{len(self.removes)} to remove", style="red"))
        text = (
            Text(" · ").join(parts)
            if parts
            else Text("Nothing picked: space picks a row.", style="dim")
        )
        closure = self.closure()
        if closure.brings:
            text.append(
                f"   brings {len(closure.brings)} more: {', '.join(closure.brings[:6])}",
                style="dim",
            )
            if len(closure.brings) > 6:
                text.append("…", style="dim")
        self.query_one("#store-cart-text", Static).update(text)
        empty = not (self.adds or self.bundles or self.removes)
        self.query_one("#store-apply", Button).disabled = empty
        self.query_one("#store-clear", Button).disabled = empty

    @on(Button.Pressed, "#store-clear")
    def clear(self) -> None:
        changed = self.adds + self.removes + list(self._bundle_members())
        self.adds, self.removes, self.bundles = [], [], []
        for item_id in changed:
            self._redraw(item_id)
        self._cart()

    @on(Button.Pressed, "#store-apply")
    def action_apply(self) -> None:
        app: DevTui = self.app  # type: ignore[assignment]
        if not (self.adds or self.bundles or self.removes):
            return
        steps = []
        if self.removes:
            steps += self.product.remove(self.removes)
        if self.adds or self.bundles:
            steps += self.product.add(self.adds, self.bundles)
        closure = self.closure()
        notes = list(closure.notes)
        if closure.unavailable:
            notes.append(
                f"Not available here, and skipped: {', '.join(closure.unavailable)}"
            )

        def confirmed(ok: bool | None) -> None:
            if ok:
                app.run_steps(
                    "Store",
                    steps,
                    after=self.product.after_add()
                    if (self.adds or self.bundles)
                    else [],
                    on_done=self._done,
                )

        app.push_screen(ConfirmScreen("Apply these changes?", steps, notes), confirmed)

    def _done(self, ok: bool) -> None:
        if ok:
            self.clear()
        self.installed = self.product.installed()
        self._detail_for = ""
        self.refresh_rows()

    @on(Button.Pressed, "#store-cart Button")
    def _form(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("store-form-"):
            return
        app: DevTui = self.app  # type: ignore[assignment]
        form = self.product.forms()[int(button_id.rsplit("-", 1)[-1])]

        def filled(values: dict[str, str] | None) -> None:
            if values is not None:
                from devtui.plugin import Step

                app.run_steps(
                    form.label,
                    [Step(tuple(form.build(values)))],
                    after=self.product.after_add(),
                    on_done=self._done,
                )

        app.push_screen(FormScreen(form), filled)
