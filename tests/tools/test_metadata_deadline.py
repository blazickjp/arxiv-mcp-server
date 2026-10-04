"""Tests for metadata deadline enforcement (PR #285 P1 fix)."""

import time
from unittest.mock import Mock, patch
import pytest
import requests.exceptions
from arxiv_mcp_server.tools.download import _fetch_arxiv_metadata


def test_metadata_deadline_skips_when_insufficient_budget_before_gate():
    """Pre-gate check skips metadata lookup when budget insufficient."""
    deadline = time.monotonic() + 2.0  # Only 2s budget

    with patch("arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER") as mock_limiter:
        # pending_wait=3s (typical), min_attempt_time=1s → need 4s, have 2s → skip
        mock_limiter.seconds_until_next_slot.return_value = 3.0

        result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    assert result is None
    # Should not have called run_sync at all
    mock_limiter.run_sync.assert_not_called()


def test_metadata_with_retries_zero_makes_exactly_one_request():
    """With ARXIV_MAX_RETRIES=0, metadata lookup makes exactly 1 request (not 4)."""
    request_count = 0

    with patch("arxiv_mcp_server.tools.download.get_arxiv_client") as mock_get_client:
        with patch(
            "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
        ) as mock_limiter:
            # Verify get_arxiv_client is called with num_retries=0
            mock_client = Mock()
            mock_get_client.return_value = mock_client

            def run_sync_immediate(operation):
                return operation()

            mock_limiter.run_sync.side_effect = run_sync_immediate
            mock_limiter.seconds_until_next_slot.return_value = 0.0

            # Mock client.results to track calls
            def mock_results(search):
                nonlocal request_count
                request_count += 1
                # Simulate 406 response (would retry with default client)
                raise StopIteration("No results")

            mock_client.results = mock_results

            with patch("arxiv_mcp_server.tools.download.arxiv.Search"):
                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

            # Should have called get_arxiv_client with num_retries=0
            mock_get_client.assert_called_once_with(num_retries=0)
            # Should have made exactly 1 request (not 4 with default retries)
            assert request_count == 1


def test_metadata_returns_none_on_timeout_no_thread_leak():
    """Timeout returns None and doesn't leave thread holding gate."""
    with patch("arxiv_mcp_server.tools.download.get_arxiv_client") as mock_get_client:
        with patch(
            "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
        ) as mock_limiter:
            mock_client = Mock()
            mock_session = Mock(spec=requests.Session)
            mock_client._session = mock_session
            mock_get_client.return_value = mock_client

            # Simulate timeout
            mock_session.get = Mock(
                side_effect=requests.exceptions.ReadTimeout("Timeout")
            )

            gate_released = False

            def run_sync_that_releases_on_exception(operation):
                nonlocal gate_released
                try:
                    return operation()
                finally:
                    gate_released = True

            mock_limiter.run_sync.side_effect = run_sync_that_releases_on_exception
            mock_limiter.seconds_until_next_slot.return_value = 0.0

            with patch("arxiv_mcp_server.tools.download.arxiv.Search"):
                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

            # Should return None on timeout
            assert result is None
            # Gate should be released (finally block executed)
            assert gate_released


def test_metadata_no_extra_406_retries():
    """HTTP 406 with retries=0 doesn't add extra retries (issue #277)."""
    # This test verifies that we're using num_retries=0 and not adding
    # extra 406-specific retry logic in the metadata path
    with patch("arxiv_mcp_server.tools.download.get_arxiv_client") as mock_get_client:
        mock_get_client.return_value = Mock()

        _fetch_arxiv_metadata("1234.5678", deadline=None)

        # Must use num_retries=0 (issue #277: 406 is IP-level throttling)
        mock_get_client.assert_called_once_with(num_retries=0)


def test_metadata_deadline_patches_session_get_with_timeout():
    """With deadline, session.get is patched to clamp timeouts."""
    deadline = time.monotonic() + 7.0

    with patch("arxiv_mcp_server.tools.download.get_arxiv_client") as mock_get_client:
        with patch(
            "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
        ) as mock_limiter:
            mock_client = Mock()
            mock_session = Mock(spec=requests.Session)
            original_get = Mock()
            mock_session.get = original_get
            mock_client._session = mock_session
            mock_get_client.return_value = mock_client

            mock_limiter.seconds_until_next_slot.return_value = 0.0

            patched_get_was_different = False

            def run_sync_check_patch(operation):
                nonlocal patched_get_was_different
                # During operation, session.get should be patched
                patched_get_was_different = mock_session.get != original_get
                # Call operation to trigger the patch
                try:
                    return operation()
                except StopIteration:
                    return None

            mock_limiter.run_sync.side_effect = run_sync_check_patch

            with patch("arxiv_mcp_server.tools.download.arxiv.Search"):
                with patch.object(mock_client, "results", return_value=iter([])):
                    _fetch_arxiv_metadata("1234.5678", deadline=deadline)

            # session.get should have been patched during the operation
            assert (
                patched_get_was_different
            ), "session.get should be patched for deadline enforcement"
            # After operation, session.get should be restored
            assert (
                mock_session.get == original_get
            ), "session.get should be restored after operation"
