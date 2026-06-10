from axitools.cogs._paginated_select import _Pager


def test_pagination_splits_options_into_pages():
    options = [(str(i), f"item {i}") for i in range(60)]
    pager = _Pager(options, page_size=25)
    assert pager.page_count == 3
    assert len(pager.current_options()) == 25
    pager.next_page()
    assert pager.current_index == 1
    pager.next_page(); pager.next_page()  # clamps at last page
    assert pager.current_index == 2
    assert len(pager.current_options()) == 10
