"""Tests for configuration settings."""

import os
import pytest
from unittest.mock import patch


def test_request_timeout_fallback():
    """ARXIV_REQUEST_TIMEOUT falls back to legacy REQUEST_TIMEOUT when not explicitly set."""
    from arxiv_mcp_server.config import Settings

    # Test 1: ARXIV_REQUEST_TIMEOUT explicitly set (takes precedence)
    with patch.dict(
        os.environ,
        {"ARXIV_REQUEST_TIMEOUT": "15", "REQUEST_TIMEOUT": "45"},
        clear=False,
    ):
        settings = Settings()
        assert settings.get_request_timeout() == 15

    # Test 2: Only REQUEST_TIMEOUT set (fallback)
    with patch.dict(os.environ, {"REQUEST_TIMEOUT": "25"}, clear=False):
        # Clear ARXIV_REQUEST_TIMEOUT from env if present
        env = os.environ.copy()
        if "ARXIV_REQUEST_TIMEOUT" in env:
            del env["ARXIV_REQUEST_TIMEOUT"]
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.get_request_timeout() == 25

    # Test 3: Neither set (use ARXIV_REQUEST_TIMEOUT default of 30)
    env = os.environ.copy()
    if "ARXIV_REQUEST_TIMEOUT" in env:
        del env["ARXIV_REQUEST_TIMEOUT"]
    if "REQUEST_TIMEOUT" in env:
        del env["REQUEST_TIMEOUT"]
    with patch.dict(os.environ, env, clear=True):
        settings = Settings()
        assert settings.get_request_timeout() == 30
