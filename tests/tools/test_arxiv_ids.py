"""Unit tests for shared arXiv ID normalize + validate helpers."""

import pytest

from arxiv_mcp_server.tools.arxiv_ids import (
    arxiv_version_number,
    arxiv_version_suffix,
    bare_arxiv_id,
    filesystem_arxiv_stem,
    is_valid_arxiv_id,
    logical_arxiv_id_from_stem,
    normalize_arxiv_id,
    parse_arxiv_id,
)
from arxiv_mcp_server.tools.list_papers import is_valid_arxiv_id as list_reexport


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1706.03762", "1706.03762"),
        ("1706.03762v7", "1706.03762v7"),
        ("  1706.03762v7  ", "1706.03762v7"),
        ("arxiv:1706.03762v7", "1706.03762v7"),
        ("arXiv:1706.03762v7", "1706.03762v7"),
        ("ARXIV:1706.03762", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762v7", "1706.03762v7"),
        ("http://arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://www.arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762.pdf", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762v7.pdf", "1706.03762v7"),
        ("arxiv.org/abs/1706.03762", "1706.03762"),
        ("hep-th/9901001", "hep-th/9901001"),
        ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001"),
        ("https://arxiv.org/pdf/hep-th/9901001.pdf", "hep-th/9901001"),
        ("arxiv:hep-th/9901001v1", "hep-th/9901001v1"),
    ],
)
def test_normalize_arxiv_id(raw, expected):
    assert normalize_arxiv_id(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("arxiv:1706.03762v7", "1706.03762v7"),
        ("https://arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762v7.pdf", "1706.03762v7"),
        ("hep-th/9901001", "hep-th/9901001"),
        ("2401.12345", "2401.12345"),
    ],
)
def test_parse_arxiv_id_accepts_wrappers(raw, expected):
    assert parse_arxiv_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not-a-paper", "garbage!!!", "arxiv:not-a-paper", "1234", "abcd"],
)
def test_parse_arxiv_id_rejects_garbage(raw):
    assert parse_arxiv_id(raw) is None


def test_is_valid_arxiv_id_reexported_from_list_papers():
    """Existing importers of list_papers.is_valid_arxiv_id keep working."""
    assert list_reexport is is_valid_arxiv_id
    assert is_valid_arxiv_id("1706.03762v7")
    assert is_valid_arxiv_id("hep-th/9901001")
    assert not is_valid_arxiv_id("not-a-paper")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1706.03762", "1706.03762"),
        ("1706.03762v7", "1706.03762"),
        ("hep-th/9901001v1", "hep-th/9901001"),
        ("2401.12345v12", "2401.12345"),
    ],
)
def test_bare_arxiv_id(raw, expected):
    assert bare_arxiv_id(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1706.03762", None),
        ("1706.03762v7", "v7"),
        ("1706.03762V7", "v7"),
        ("hep-th/9901001v1", "v1"),
    ],
)
def test_arxiv_version_suffix(raw, expected):
    assert arxiv_version_suffix(raw) == expected


def test_arxiv_version_number_orders_versions():
    assert arxiv_version_number("1706.03762") == -1
    assert arxiv_version_number("1706.03762v3") < arxiv_version_number("1706.03762v7")


@pytest.mark.parametrize(
    "logical, stem",
    [
        ("hep-th/9901001", "hep-th__9901001"),
        ("quant-ph/0101001v2", "quant-ph__0101001v2"),
        ("1706.03762v7", "1706.03762v7"),
    ],
)
def test_filesystem_stem_roundtrip(logical, stem):
    assert filesystem_arxiv_stem(logical) == stem
    assert logical_arxiv_id_from_stem(stem) == logical
