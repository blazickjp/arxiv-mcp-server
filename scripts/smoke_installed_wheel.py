#!/usr/bin/env python3
"""Build-artifact smoke test for an isolated arxiv-mcp-server wheel."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
from datetime import timedelta
from typing import Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
MCP_REQUEST_TIMEOUT_SECONDS = 30
MCP_OVERALL_TIMEOUT_SECONDS = 120
EXPECTED_TOOLS = {
    "check_alerts",
    "citation_graph",
    "download_paper",
    "export_citations",
    "get_abstract",
    "get_paper_latex",
    "get_paper_latex_section",
    "get_paper_outline",
    "list_paper_latex_sections",
    "list_papers",
    "list_watches",
    "read_paper",
    "read_paper_section",
    "reindex",
    "search_paper_text",
    "search_papers",
    "semantic_search",
    "unwatch_topic",
    "watch_topic",
}
PROMPT_ARGUMENTS = {
    "compare_papers": {"paper_ids": "1706.03762, 1810.04805"},
    "deep-paper-analysis": {"paper_id": "1706.03762"},
    "literature-synthesis": {"paper_ids": "1706.03762, 1810.04805"},
    "literature_review": {"topic": "attention mechanisms"},
    "research-discovery": {"topic": "attention mechanisms"},
    "research-question": {
        "paper_ids": "1706.03762, 1810.04805",
        "topic": "attention mechanisms",
    },
    "summarize_paper": {"paper_id": "1706.03762"},
}


def find_single_wheel(dist_dir: Path) -> Path:
    """Return the one wheel in *dist_dir*, rejecting stale artifact mixtures."""
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"Expected exactly one wheel in {dist_dir}, found {len(wheels)}: "
            f"{[wheel.name for wheel in wheels]}"
        )
    return wheels[0]


def venv_python(venv_dir: Path, *, platform: str = os.name) -> Path:
    """Return the Python executable path for a POSIX or Windows virtualenv."""
    if platform == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_entrypoint(venv_dir: Path, *, platform: str = os.name) -> Path:
    """Return the installed console-script path for a POSIX or Windows virtualenv."""
    if platform == "nt":
        return venv_dir / "Scripts" / "arxiv-mcp-server.exe"
    return venv_dir / "bin" / "arxiv-mcp-server"


def clean_subprocess_env(
    home: Path, *, source: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Create a subprocess environment without ambient Python contamination."""
    source = os.environ if source is None else source
    blocked = {
        "ALLOWED_HOSTS",
        "ALLOWED_ORIGINS",
        "APP_NAME",
        "APP_VERSION",
        "BATCH_SIZE",
        "HOST",
        "MAX_RESULTS",
        "PIP_CONSTRAINT",
        "PORT",
        "PYTHONHOME",
        "PYTHONPATH",
        "REQUEST_TIMEOUT",
        "TRANSPORT",
        "UV_CONSTRAINT",
        "UV_OVERRIDE",
        "UV_REQUIRE_HASHES",
        "VIRTUAL_ENV",
    }
    env = {key: value for key, value in source.items() if key not in blocked}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["NO_COLOR"] = "1"
    return env


def canonical_version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


async def exercise_mcp_server(
    parameters: StdioServerParameters, *, expected_version: str
) -> dict[str, object]:
    """Exercise the complete public MCP discovery and prompt surface."""
    async with asyncio.timeout(MCP_OVERALL_TIMEOUT_SECONDS):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=MCP_REQUEST_TIMEOUT_SECONDS),
            ) as session:
                initialized = await session.initialize()
                server_version = initialized.serverInfo.version
                if server_version != expected_version:
                    raise RuntimeError(
                        f"MCP server version {server_version!r} does not match "
                        f"expected version {expected_version!r}"
                    )

                tools = await session.list_tools()
                tool_names = {tool.name for tool in tools.tools}
                if tool_names != EXPECTED_TOOLS:
                    raise RuntimeError(
                        "MCP server exposed an unexpected tool set: "
                        f"missing={sorted(EXPECTED_TOOLS - tool_names)}, "
                        f"extra={sorted(tool_names - EXPECTED_TOOLS)}"
                    )

                prompts = await session.list_prompts()
                prompt_names = {prompt.name for prompt in prompts.prompts}
                if prompt_names != set(PROMPT_ARGUMENTS):
                    raise RuntimeError(
                        "MCP server exposed an unexpected prompt set: "
                        f"missing={sorted(set(PROMPT_ARGUMENTS) - prompt_names)}, "
                        f"extra={sorted(prompt_names - set(PROMPT_ARGUMENTS))}"
                    )
                prompt_lengths = {}
                for prompt_name, arguments in PROMPT_ARGUMENTS.items():
                    result = await session.get_prompt(prompt_name, arguments)
                    prompt_lengths[prompt_name] = sum(
                        len(getattr(message.content, "text", ""))
                        for message in result.messages
                    )
                    if prompt_lengths[prompt_name] == 0:
                        raise RuntimeError(f"Prompt {prompt_name!r} returned no text")

                local_result = await session.call_tool("list_papers", {})
                if local_result.isError or not local_result.content:
                    raise RuntimeError("list_papers failed through the MCP session")

    return {
        "server_version": server_version,
        "tools": len(tool_names),
        "prompts": len(prompt_names),
        "prompt_chars": prompt_lengths,
        "list_papers_content_items": len(local_result.content),
    }


async def smoke_wheel(wheel: Path) -> dict[str, object]:
    """Install *wheel* in a blank venv and exercise its real MCP interface."""
    with tempfile.TemporaryDirectory(prefix="arxiv-wheel-smoke-") as temp:
        temp_dir = Path(temp)
        venv_dir = temp_dir / "venv"
        home_dir = temp_dir / "home"
        storage_dir = temp_dir / "papers"
        home_dir.mkdir()
        storage_dir.mkdir()
        env = clean_subprocess_env(home_dir)

        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(venv_dir)],
            check=True,
            env=env,
            cwd=temp_dir,
        )
        python = venv_python(venv_dir)
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheel)],
            check=True,
            env=env,
            cwd=temp_dir,
        )
        installed_version = subprocess.check_output(
            [
                str(python),
                "-c",
                "import importlib.metadata; "
                "print(importlib.metadata.version('arxiv-mcp-server'))",
            ],
            text=True,
            env=env,
            cwd=temp_dir,
        ).strip()

        entrypoint = venv_entrypoint(venv_dir)
        if not entrypoint.is_file():
            raise RuntimeError(
                f"Installed wheel is missing console script {entrypoint}"
            )
        parameters = StdioServerParameters(
            command=str(entrypoint),
            args=[
                "--storage-path",
                str(storage_dir),
            ],
            env=env,
            cwd=temp_dir,
        )
        expected_version = canonical_version()
        if installed_version != expected_version:
            raise RuntimeError(
                "Version mismatch: "
                f"project={expected_version}, installed={installed_version}"
            )
        protocol = await exercise_mcp_server(
            parameters, expected_version=expected_version
        )

        return {
            "wheel": wheel.name,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": sys.platform,
            "version": installed_version,
            **protocol,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist",
        help="Directory containing exactly one candidate wheel",
    )
    args = parser.parse_args()
    wheel = find_single_wheel(args.dist_dir)
    print(json.dumps(asyncio.run(smoke_wheel(wheel)), sort_keys=True))


if __name__ == "__main__":
    main()
