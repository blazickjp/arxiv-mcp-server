"""Tests for bounded-by-default paper content pagination (#127)."""

import json

import pytest

from arxiv_mcp_server.tools.content import (
    DEFAULT_MAX_CHARS,
    add_content_payload,
    bound_arguments,
    paginate_content,
)


def test_default_max_chars_is_in_accepted_band():
    """Issue #127 asks for roughly 8,000–12,000 characters by default."""
    assert 8_000 <= DEFAULT_MAX_CHARS <= 12_000


def test_bound_arguments_applies_default_when_max_chars_omitted():
    assert bound_arguments({})["max_chars"] == DEFAULT_MAX_CHARS
    assert bound_arguments({"start": 10})["max_chars"] == DEFAULT_MAX_CHARS
    assert bound_arguments({"max_chars": None})["max_chars"] == DEFAULT_MAX_CHARS


def test_bound_arguments_preserves_caller_specified_max_chars():
    """Explicit max_chars must not be clamped or rewritten (#127)."""
    assert bound_arguments({"max_chars": 50})["max_chars"] == 50
    assert bound_arguments({"max_chars": 100_000})["max_chars"] == 100_000
    assert bound_arguments({"max_chars": 1})["max_chars"] == 1


def test_bound_arguments_return_full_text_opts_into_unbounded():
    bounded = bound_arguments({"return_full_text": True, "max_chars": 50})
    assert "max_chars" not in bounded
    assert bounded["return_full_text"] is True

    bounded = bound_arguments({"return_full_text": True})
    assert "max_chars" not in bounded


def test_paginate_short_paper_returns_entire_body_under_default():
    content = "short paper body"
    page = paginate_content(content, bound_arguments({}))
    assert page["content"] == content
    assert page["content_length"] == len(content)
    assert page["start"] == 0
    assert page["returned_chars"] == len(content)
    assert page["next_start"] is None
    assert page["is_truncated"] is False
    assert "next_retrieval" not in page


def test_paginate_long_paper_defaults_to_bounded_chunk():
    content = "x" * (DEFAULT_MAX_CHARS + 5_000)
    page = paginate_content(content, bound_arguments({}))
    assert page["content_length"] == len(content)
    assert page["start"] == 0
    assert page["returned_chars"] == DEFAULT_MAX_CHARS
    assert page["next_start"] == DEFAULT_MAX_CHARS
    assert page["is_truncated"] is True
    assert page["content"] == content[:DEFAULT_MAX_CHARS]
    assert "next_start" in page["next_retrieval"]


def test_paginate_explicit_limit_and_continuation():
    content = "abcdefghijklmnopqrstuvwxyz"
    first = paginate_content(content, bound_arguments({"max_chars": 10}))
    assert first["returned_chars"] == 10
    assert first["next_start"] == 10
    assert first["is_truncated"] is True
    assert first["content"] == "abcdefghij"

    second = paginate_content(
        content, bound_arguments({"start": first["next_start"], "max_chars": 10})
    )
    assert second["start"] == 10
    assert second["content"] == "klmnopqrst"
    assert second["next_start"] == 20
    assert second["is_truncated"] is True

    final = paginate_content(
        content, bound_arguments({"start": second["next_start"], "max_chars": 10})
    )
    assert final["start"] == 20
    assert final["content"] == "uvwxyz"
    assert final["returned_chars"] == 6
    assert final["next_start"] is None
    assert final["is_truncated"] is False
    assert "next_retrieval" not in final


def test_paginate_end_of_content_and_invalid_offsets():
    content = "abcdefghij"
    past_end = paginate_content(
        content, bound_arguments({"start": 100, "max_chars": 5})
    )
    assert past_end["start"] == len(content)
    assert past_end["returned_chars"] == 0
    assert past_end["next_start"] is None
    assert past_end["is_truncated"] is False
    assert past_end["content"] == ""

    negative_coerced = paginate_content(
        content, bound_arguments({"start": -5, "max_chars": 3})
    )
    assert negative_coerced["start"] == 0
    assert negative_coerced["content"] == "abc"

    invalid_max = paginate_content(content, {"start": 0, "max_chars": "nope"})
    # Invalid max_chars is ignored by paginate_content itself (treated as full).
    assert invalid_max["returned_chars"] == len(content)


def test_paginate_full_text_opt_in_returns_entire_long_paper():
    content = "y" * (DEFAULT_MAX_CHARS + 2_500)
    page = paginate_content(content, bound_arguments({"return_full_text": True}))
    assert page["returned_chars"] == len(content)
    assert page["is_truncated"] is False
    assert page["next_start"] is None
    assert page["content"] == content


def test_add_content_payload_applies_default_bound_and_preserves_metadata():
    content = "z" * (DEFAULT_MAX_CHARS + 100)
    warning = "[WARN]\n\n"
    payload = add_content_payload({"status": "success"}, content, {}, warning)
    assert payload["status"] == "success"
    assert payload["content_length"] == len(content)
    assert payload["start"] == 0
    assert payload["returned_chars"] == DEFAULT_MAX_CHARS
    assert payload["next_start"] == DEFAULT_MAX_CHARS
    assert payload["is_truncated"] is True
    assert payload["next_retrieval"]
    # First page: warning is a separate field; content is pure paper text (#215).
    assert payload["content_warning"] == warning.rstrip()
    assert payload["content"] == content[:DEFAULT_MAX_CHARS]
    assert not payload["content"].startswith("[WARN]")


def test_add_content_payload_omits_warning_on_continuation_pages():
    """Continuation pages must not re-emit the untrusted banner (#215)."""
    content = "abcdefghijklmnopqrstuvwxyz"
    warning = "[UNTRUSTED EXTERNAL CONTENT — test.]\n\n"

    first = add_content_payload({}, content, {"max_chars": 10}, warning)
    assert first["start"] == 0
    assert first["content"] == "abcdefghij"
    assert first["content_warning"] == warning.rstrip()
    assert "UNTRUSTED" not in first["content"]

    second = add_content_payload({}, content, {"start": 10, "max_chars": 10}, warning)
    assert second["start"] == 10
    assert second["content"] == "klmnopqrst"
    assert "content_warning" not in second
    assert not second["content"].startswith("[UNTRUSTED")

    third = add_content_payload({}, content, {"start": 20, "max_chars": 10}, warning)
    assert third["start"] == 20
    assert third["content"] == "uvwxyz"
    assert "content_warning" not in third


def test_add_content_payload_stitched_pages_are_contiguous_paper_text():
    """Stitching start=0,10,20 with max_chars=10 yields the original paper (#215)."""
    content = "abcdefghijklmnopqrstuvwxyz"
    warning = "[UNTRUSTED EXTERNAL CONTENT — test.]\n\n"
    chunks = []
    start = 0
    while True:
        page = add_content_payload(
            {}, content, {"start": start, "max_chars": 10}, warning
        )
        chunks.append(page["content"])
        assert "UNTRUSTED" not in page["content"]
        if page["start"] == 0:
            assert "content_warning" in page
        else:
            assert "content_warning" not in page
        if page["next_start"] is None:
            break
        start = page["next_start"]
    assert "".join(chunks) == content
