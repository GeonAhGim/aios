import pytest
from pydantic import ValidationError

from src.api.contracts.pagination import PageMeta, PageParams


def test_default_page_params():
    params = PageParams()
    assert params.page == 1
    assert params.size == 20
    assert params.offset == 0


def test_offset_computed_from_page_and_size():
    params = PageParams(page=3, size=10)
    assert params.offset == 20


def test_page_below_one_rejected():
    with pytest.raises(ValidationError):
        PageParams(page=0)


def test_size_above_cap_rejected():
    with pytest.raises(ValidationError):
        PageParams(size=101)


def test_page_meta_allows_total_none_for_cursor_style_lists():
    meta = PageMeta(total=None, page=None, size=50, next_cursor="abc")
    assert meta.total is None
