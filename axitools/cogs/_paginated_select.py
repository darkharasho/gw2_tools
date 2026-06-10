"""Reusable paginated dropdown select view for Discord UIs.

``PaginatedSelectView`` wraps a :class:`discord.ui.Select` whose options are
split across pages of at most ``page_size`` entries (Discord caps selects at 25
options). When more than one page of options exists, secondary "Previous" /
"Next" buttons are added that rebuild the select for the current page.

The view exposes a small, side-effect-free surface for unit testing
(:attr:`page_count`, :attr:`current_index`, :meth:`current_options`,
:meth:`next_page`, :meth:`prev_page`) alongside the live Discord glue.
"""
from __future__ import annotations

import asyncio
from typing import Callable, List, Optional, Sequence, Tuple

import discord

# Discord limits a select menu to 25 options per page.
PAGE_SIZE = 25
# Match the timeout previously used by the bespoke RSS views.
TIMEOUT = 120

# (value, label) for the simple form used by tests; descriptions are optional.
OptionTuple = Tuple[str, str]


class PaginatedSelectView(discord.ui.View):
    """A :class:`discord.ui.View` presenting paginated select options.

    Parameters
    ----------
    options:
        Sequence of ``(value, label)`` tuples. ``value`` is returned to the
        ``on_select`` callback; ``label`` is shown in the dropdown.
    page_size:
        Maximum options per page (defaults to :data:`PAGE_SIZE`, i.e. 25).
    on_select:
        Callable invoked as ``on_select(value, interaction)`` when the user
        picks an option. May be a coroutine function or a plain callable; the
        result is awaited if awaitable. Only invoked from the live Select
        callback.
    descriptions:
        Optional mapping from ``value`` to an option description string.
    placeholder:
        Base placeholder text for the select. When multiple pages exist a
        ``(page X/Y)`` suffix is appended automatically.
    invoker_id:
        If provided, only this user may interact with the view.
    """

    PAGE_SIZE = PAGE_SIZE

    async def _init_view(self) -> None:
        super().__init__(timeout=TIMEOUT)

    def __init__(
        self,
        *,
        options: Sequence[OptionTuple],
        page_size: int = PAGE_SIZE,
        on_select: Callable[[str, discord.Interaction], object],
        descriptions: Optional[dict] = None,
        placeholder: str = "Select an option",
        invoker_id: Optional[int] = None,
    ) -> None:
        # discord.ui.View.__init__ requires a running event loop (it creates an
        # internal future). Provide a transient loop when instantiated outside
        # one (e.g. synchronous unit tests) so behaviour matches the live path.
        try:
            asyncio.get_running_loop()
            super().__init__(timeout=TIMEOUT)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._init_view())
            finally:
                loop.close()
        self._options: List[OptionTuple] = list(options)
        self._page_size = max(1, page_size)
        self._on_select = on_select
        self._descriptions = descriptions or {}
        self._placeholder = placeholder
        self._invoker_id = invoker_id
        self._page = 0

        self._select = _PaginatedSelect(self)
        self.add_item(self._select)

        self._previous_button: Optional[_PaginatedPageButton]
        self._next_button: Optional[_PaginatedPageButton]
        if self.page_count > 1:
            self._previous_button = _PaginatedPageButton(self, -1)
            self._next_button = _PaginatedPageButton(self, 1)
            self.add_item(self._previous_button)
            self.add_item(self._next_button)
        else:
            self._previous_button = None
            self._next_button = None

        self._refresh_select()
        self._refresh_navigation()

    # -- testable surface -------------------------------------------------
    @property
    def page_count(self) -> int:
        if not self._options:
            return 1
        return (len(self._options) - 1) // self._page_size + 1

    @property
    def current_index(self) -> int:
        return self._page

    def current_options(self) -> List[OptionTuple]:
        start = self._page * self._page_size
        end = start + self._page_size
        return self._options[start:end]

    def next_page(self) -> None:
        self._page = min(self._page + 1, self.page_count - 1)

    def prev_page(self) -> None:
        self._page = max(self._page - 1, 0)

    # -- discord glue -----------------------------------------------------
    def _refresh_select(self) -> None:
        total_pages = self.page_count
        select_options: List[discord.SelectOption] = []
        for value, label in self.current_options():
            description = self._descriptions.get(value)
            select_options.append(
                discord.SelectOption(
                    label=str(label)[:100],
                    value=value,
                    description=description if description else None,
                )
            )
        self._select.options = select_options
        if total_pages > 1:
            self._select.placeholder = (
                f"{self._placeholder} (page {self._page + 1}/{total_pages})"
            )
        else:
            self._select.placeholder = self._placeholder

    def _refresh_navigation(self) -> None:
        if not self._previous_button or not self._next_button:
            return
        total_pages = self.page_count
        self._previous_button.disabled = self._page <= 0
        self._next_button.disabled = self._page >= total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # pragma: no cover - UI glue
        if self._invoker_id is not None and interaction.user.id != self._invoker_id:
            await interaction.response.send_message(
                "Only the user who invoked this command can use the dropdown.",
                ephemeral=True,
            )
            return False
        return True

    def disable(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                child.disabled = True
        self.stop()

    async def change_page(
        self, interaction: discord.Interaction, step: int
    ) -> None:  # pragma: no cover - UI glue
        previous = self._page
        if step < 0:
            self.prev_page()
        elif step > 0:
            self.next_page()
        if self._page == previous:
            await interaction.response.defer()
            return
        self._refresh_select()
        self._refresh_navigation()
        await interaction.response.edit_message(view=self)


class _PaginatedSelect(discord.ui.Select):
    def __init__(self, view: PaginatedSelectView) -> None:
        super().__init__(placeholder="", options=[], min_values=1, max_values=1)
        self._parent = view

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI glue
        value = self.values[0]
        result = self._parent._on_select(value, interaction)
        if hasattr(result, "__await__"):
            await result


class _PaginatedPageButton(discord.ui.Button):
    def __init__(self, view: PaginatedSelectView, step: int) -> None:
        label = "Previous" if step < 0 else "Next"
        super().__init__(style=discord.ButtonStyle.secondary, label=label)
        self._parent = view
        self._step = step

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI glue
        await self._parent.change_page(interaction, self._step)
