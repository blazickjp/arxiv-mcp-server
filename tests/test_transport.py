"""Tests for MCP transport selection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arxiv_mcp_server import server as server_module
from arxiv_mcp_server.config import Settings


@pytest.mark.asyncio
async def test_main_defaults_to_stdio_transport():
    """The default transport remains stdio for existing MCP clients."""
    with (
        patch.object(server_module, "settings", Settings()),
        patch.object(server_module, "_run_stdio", new_callable=AsyncMock) as mock_stdio,
        patch.object(
            server_module, "_run_streamable_http", new_callable=AsyncMock
        ) as mock_http,
    ):
        await server_module.main()

    mock_stdio.assert_awaited_once()
    mock_http.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "streamable-http", "streamable_http"])
async def test_main_runs_streamable_http_transport(transport):
    """HTTP transport aliases select the Streamable HTTP runner."""
    settings = Settings(TRANSPORT=transport)
    with (
        patch.object(server_module, "settings", settings),
        patch.object(server_module, "_run_stdio", new_callable=AsyncMock) as mock_stdio,
        patch.object(
            server_module, "_run_streamable_http", new_callable=AsyncMock
        ) as mock_http,
    ):
        await server_module.main()

    mock_stdio.assert_not_awaited()
    mock_http.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_rejects_unknown_transport():
    """Invalid transport values fail loudly instead of silently starting stdio."""
    with patch.object(server_module, "settings", Settings(TRANSPORT="websocket")):
        with pytest.raises(ValueError, match="Unsupported transport"):
            await server_module.main()


@pytest.mark.asyncio
async def test_streamable_http_uses_configured_host_port():
    """The HTTP runner builds a Starlette app and starts uvicorn with settings."""
    settings = Settings(TRANSPORT="http", HOST="127.0.0.1", PORT=8765)

    session_manager = MagicMock()
    session_context = AsyncMock()
    session_manager.run.return_value = session_context
    session_manager.handle_request = AsyncMock()

    uvicorn_server = MagicMock()
    uvicorn_server.serve = AsyncMock()

    with (
        patch.object(server_module, "settings", settings),
        patch.object(
            server_module, "StreamableHTTPSessionManager", return_value=session_manager
        ) as manager_class,
        patch.object(server_module.uvicorn, "Config") as config_class,
        patch.object(
            server_module.uvicorn, "Server", return_value=uvicorn_server
        ) as server_class,
    ):
        await server_module._run_streamable_http()

    _, manager_kwargs = manager_class.call_args
    assert manager_kwargs["app"] is server_module.server
    assert manager_kwargs["event_store"] is None
    assert manager_kwargs["json_response"] is False
    security_settings = manager_kwargs["security_settings"]
    assert security_settings.enable_dns_rebinding_protection is True
    assert "127.0.0.1:8765" in security_settings.allowed_hosts
    assert "localhost:8765" in security_settings.allowed_hosts
    config_class.assert_called_once()
    _, config_kwargs = config_class.call_args
    assert config_kwargs["host"] == "127.0.0.1"
    assert config_kwargs["port"] == 8765
    server_class.assert_called_once_with(config_class.return_value)
    session_manager.run.assert_called_once()
    uvicorn_server.serve.assert_awaited_once()


def test_transport_security_allows_loopback_and_configured_hosts():
    """HTTP transport enables DNS rebinding protection with explicit allowlists."""
    settings = Settings(
        HOST="0.0.0.0",
        PORT=9000,
        ALLOWED_HOSTS="arxiv.example.com, arxiv.example.com:443",
        ALLOWED_ORIGINS="https://arxiv.example.com",
    )
    with patch.object(server_module, "settings", settings):
        security_settings = server_module._transport_security_settings()

    assert security_settings.enable_dns_rebinding_protection is True
    assert "0.0.0.0:9000" in security_settings.allowed_hosts
    assert "127.0.0.1:9000" in security_settings.allowed_hosts
    assert "localhost:9000" in security_settings.allowed_hosts
    assert "arxiv.example.com" in security_settings.allowed_hosts
    assert "arxiv.example.com:443" in security_settings.allowed_hosts
    assert "http://127.0.0.1:9000" in security_settings.allowed_origins
    assert "http://localhost:9000" in security_settings.allowed_origins
    assert "https://arxiv.example.com" in security_settings.allowed_origins


def test_http_defaults_bind_to_localhost():
    """HTTP mode should not expose an unauthenticated MCP server by default."""
    settings = Settings()
    assert settings.TRANSPORT == "stdio"
    assert settings.HOST == "127.0.0.1"
    assert settings.PORT == 8000
