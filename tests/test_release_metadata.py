import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_and_arxiv_dependency_are_synchronized():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    project = pyproject["project"]
    version = project["version"]
    arxiv_requirement = next(
        dependency
        for dependency in project["dependencies"]
        if dependency.startswith("arxiv")
    )

    assert version == "0.5.1"
    assert server["$schema"] == (
        "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    )
    assert server["version"] == version
    assert server["packages"][0]["version"] == version
    assert arxiv_requirement == "arxiv>=2.1.0,<4"
