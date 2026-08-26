import json
import re
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_and_arxiv_dependency_are_synchronized():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    codex_plugin = json.loads(
        (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    claude_plugin = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    claude_marketplace = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    codex_marketplace = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    codex_mcp = json.loads((ROOT / ".codex-mcp.json").read_text(encoding="utf-8"))
    shared_mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    kiro_mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    kiro_power = (ROOT / "POWER.md").read_text(encoding="utf-8")
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    project = pyproject["project"]
    version = project["version"]
    arxiv_requirement = next(
        dependency
        for dependency in project["dependencies"]
        if dependency.startswith("arxiv")
    )

    lock_package = next(
        package
        for package in lock["package"]
        if package["name"] == project["name"]
        and package.get("source") == {"editable": "."}
    )

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert server["$schema"] == (
        "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    )
    assert server["version"] == version
    assert server["packages"][0]["version"] == version
    assert manifest["version"] == version
    assert manifest["server"]["mcp_config"]["command"] == "python3.11"
    assert manifest["compatibility"]["runtimes"]["python"] == ">=3.11,<3.12"
    assert codex_plugin["version"] == version
    assert codex_plugin["name"] == project["name"]
    assert codex_plugin["mcpServers"] == "./.codex-mcp.json"
    assert codex_plugin["skills"] == "./skills/"
    assert claude_plugin["version"] == version
    assert claude_plugin["name"] == project["name"]
    assert claude_marketplace["name"] == "arxiv-mcp"
    assert claude_marketplace["plugins"][0]["name"] == project["name"]
    assert claude_marketplace["plugins"][0]["source"] == "./"
    assert codex_marketplace["name"] == claude_marketplace["name"]
    assert codex_marketplace["plugins"][0]["name"] == project["name"]
    assert codex_marketplace["plugins"][0]["source"] == {
        "source": "local",
        "path": "./",
    }
    assert codex_mcp == {
        "arxiv": {
            "command": "uvx",
            "args": ["arxiv-mcp-server"],
        }
    }
    assert shared_mcp["mcpServers"]["arxiv"] == {
        "type": "stdio",
        "command": "uvx",
        "args": ["arxiv-mcp-server"],
    }
    assert kiro_mcp["mcpServers"]["arxiv"] == {
        "command": "uvx",
        "args": ["arxiv-mcp-server"],
    }
    kiro_frontmatter = re.match(r"---\n(?P<body>.*?)\n---", kiro_power, re.DOTALL)
    assert kiro_frontmatter is not None
    assert 'name: "arxiv-mcp-server"' in kiro_frontmatter["body"]
    assert 'displayName: "arXiv Research"' in kiro_frontmatter["body"]
    assert 'author: "Joseph Blazick"' in kiro_frontmatter["body"]
    assert lock_package["version"] == version
    assert project["authors"][0]["email"] == manifest["author"]["email"]
    assert arxiv_requirement == "arxiv>=2.1.0"


def test_mcp_dependency_excludes_incompatible_major_release():
    """mcp 2.0.0 removed the low-level ``Server`` registration decorators.

    ``server.py`` registers handlers with ``@server.list_prompts()`` and
    friends, which raise ``AttributeError`` at import time under 2.x. The
    requirement must stay below 2.0.0 until those call sites are migrated to
    the ``add_request_handler`` API.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    mcp_requirement = next(
        dependency
        for dependency in pyproject["project"]["dependencies"]
        if re.match(r"^mcp\b", dependency)
    )
    assert mcp_requirement == "mcp>=1.27.0,<2.0.0"

    locked_mcp = next(
        package for package in lock["package"] if package["name"] == "mcp"
    )
    assert tuple(int(part) for part in locked_mcp["version"].split(".")[:1]) < (2,)


def test_readme_exposes_supported_install_paths():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "static.pepy.tech/badge/arxiv-mcp-server" in readme
    assert "claude mcp add --transport stdio --scope user arxiv" in readme
    assert "codex mcp add arxiv -- uvx arxiv-mcp-server" in readme
    assert "hermes mcp add arxiv --command uvx --args arxiv-mcp-server" in readme
    assert "hermes mcp test arxiv" in readme
    assert (
        "uvx --python 3.11 --refresh-package arxiv-mcp-server arxiv-mcp-server"
        in readme
    )
    assert "command -v uvx" in readme
    assert "Get-Command uvx" in readme
    assert "uv tool update-shell" in readme
    assert (
        "registry.modelcontextprotocol.io/v0.1/servers/io.github.blazickjp%2Farxiv-mcp-server/versions/latest"
        in readme
    )
    assert "claude plugin install arxiv-mcp-server@arxiv-mcp" in readme
    assert "codex plugin add arxiv-mcp-server@arxiv-mcp" in readme
    assert '$env:TRANSPORT = "http"' in readme
    assert "clients that accept the `mcpServers` JSON shape" in readme
    assert "Smithery" not in readme
    assert "smithery" not in readme

    kiro_url = re.search(
        r"https://kiro\.dev/launch/mcp/add\?[^)]+",
        readme,
    )
    assert kiro_url is not None
    kiro_query = parse_qs(urlparse(kiro_url.group()).query)
    assert kiro_query["name"] == ["arxiv-mcp-server"]
    assert json.loads(kiro_query["config"][0]) == {
        "command": "uvx",
        "args": ["arxiv-mcp-server"],
        "disabled": False,
        "autoApprove": [],
    }

    vscode_url = re.search(
        r"https://vscode\.dev/redirect/mcp/install\?[^)]+",
        readme,
    )
    assert vscode_url is not None
    vscode_query = parse_qs(urlparse(vscode_url.group()).query)
    assert vscode_query["name"] == ["arxiv-mcp-server"]
    vscode_config = {
        "type": "stdio",
        "command": "uvx",
        "args": ["arxiv-mcp-server"],
    }
    assert json.loads(vscode_query["config"][0]) == vscode_config

    insiders_url = re.search(
        r"https://insiders\.vscode\.dev/redirect/mcp/install\?[^)]+",
        readme,
    )
    assert insiders_url is not None
    insiders_query = parse_qs(urlparse(insiders_url.group()).query)
    assert insiders_query["name"] == ["arxiv-mcp-server"]
    assert insiders_query["quality"] == ["insiders"]
    assert json.loads(insiders_query["config"][0]) == vscode_config


def test_claude_manifests_reference_live_schemastore_schemas():
    plugin = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )

    assert plugin["$schema"] == (
        "https://www.schemastore.org/claude-code-plugin-manifest.json"
    )
    assert marketplace["$schema"] == (
        "https://www.schemastore.org/claude-code-marketplace.json"
    )


def test_mcpb_manual_release_dispatch_checks_out_and_validates_the_tag():
    workflow = (ROOT / ".github" / "workflows" / "build-mcpb.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "ref: ${{ inputs.release_tag || github.event.release.tag_name || github.sha }}"
        in workflow
    )
    assert (
        "RELEASE_TAG: ${{ github.event.release.tag_name || inputs.release_tag }}"
        in workflow
    )
    assert 'expected_tag = f"v{version}"' in workflow
    assert (
        'tag_commit = subprocess.check_output(["git", "rev-list", "-n", "1", tag]'
        in workflow
    )


def test_workflows_bound_protocol_smoke_runtime():
    workflows = {
        name: (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for name in ("tests.yml", "publish.yml", "build-mcpb.yml")
    }

    assert "timeout-minutes: 20" in workflows["tests.yml"]
    assert "timeout-minutes: 20" in workflows["publish.yml"]
    assert "timeout-minutes: 30" in workflows["build-mcpb.yml"]
