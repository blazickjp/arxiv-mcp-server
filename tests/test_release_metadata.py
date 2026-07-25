import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_and_arxiv_dependency_are_synchronized():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    plugin = json.loads(
        (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
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
    assert plugin["version"] == version
    assert lock_package["version"] == version
    assert project["authors"][0]["email"] == manifest["author"]["email"]
    assert arxiv_requirement == "arxiv>=2.1.0"
