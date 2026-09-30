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


@pytest.mark.parametrize("size", [0, -1])
def test_size_below_one_rejected(size):
    with pytest.raises(ValidationError) as exc_info:
        PageParams(size=size)
    error, = exc_info.value.errors()
    assert error["loc"] == ("size",)
    assert error["type"] == "greater_than_equal"


@pytest.mark.parametrize("field", ["page", "size"])
@pytest.mark.parametrize("value", [None, "invalid", 1.5])
def test_invalid_pagination_field_rejected(field, value):
    with pytest.raises(ValidationError) as exc_info:
        PageParams.model_validate({field: value})
    error, = exc_info.value.errors()
    assert error["loc"] == (field,)
    assert error["type"] in {"int_type", "int_parsing", "int_from_float"}


@pytest.mark.parametrize("field", ["page", "size"])
def test_attribute_read_failure_injection_rejected(monkeypatch, field):
    class PaginationInput:
        page = 2
        size = 10

    def fail_read(self):
        raise RuntimeError("injected pagination input failure")

    source = PaginationInput()
    assert PageParams.model_validate(source, from_attributes=True).offset == 10
    with monkeypatch.context() as patch:
        patch.setattr(PaginationInput, field, property(fail_read))
        with pytest.raises(ValidationError) as exc_info:
            PageParams.model_validate(source, from_attributes=True)
        error, = exc_info.value.errors()
        assert error["loc"] == (field,)
        assert error["type"] == "get_attribute_error"
        assert "injected pagination input failure" in error["msg"]
    assert PageParams.model_validate(source, from_attributes=True).offset == 10


@pytest.mark.parametrize("size", [1, 100])
def test_size_boundaries_accepted(size):
    params = PageParams(page=2, size=size)
    assert params.size == size
    assert params.offset == size
