from axitools.cogs._paginated_select import PaginatedSelectView


def test_pagination_splits_options_into_pages():
    options = [(str(i), f"item {i}") for i in range(60)]
    view = PaginatedSelectView(options=options, page_size=25, on_select=lambda v, i: None)
    assert view.page_count == 3
    assert len(view.current_options()) == 25
    view.next_page()
    assert view.current_index == 1
    view.next_page(); view.next_page()  # clamps at last page
    assert view.current_index == 2
    assert len(view.current_options()) == 10
