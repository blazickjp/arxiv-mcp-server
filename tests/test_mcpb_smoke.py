from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    script = SCRIPTS / "smoke_mcpb.py"
    spec = spec_from_file_location("smoke_mcpb", script)
    assert spec is not None and spec.loader is not None
    SMOKE = module_from_spec(spec)
    spec.loader.exec_module(SMOKE)
finally:
    sys.path.pop(0)

find_single_mcpb = SMOKE.find_single_mcpb
extract_mcpb = SMOKE.extract_mcpb


def test_find_single_mcpb_requires_exactly_one_candidate(tmp_path):
    with pytest.raises(RuntimeError, match="exactly one MCPB"):
        find_single_mcpb(tmp_path)

    artifact = tmp_path / "arxiv-mcp-server-0.6.2.mcpb"
    artifact.touch()
    assert find_single_mcpb(tmp_path) == artifact

    (tmp_path / "arxiv-mcp-server-0.6.3.mcpb").touch()
    with pytest.raises(RuntimeError, match="exactly one MCPB"):
        find_single_mcpb(tmp_path)


def test_extract_mcpb_extracts_archive_contents(tmp_path):
    artifact = tmp_path / "arxiv-mcp-server-0.6.2.mcpb"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr(
            "server/arxiv_mcp_server/_bundle_version.py", 'VERSION = "0.6.2"'
        )

    destination = tmp_path / "unpacked"
    extract_mcpb(artifact, destination)

    assert (destination / "manifest.json").read_text() == "{}"
    assert (
        "0.6.2"
        in (
            destination / "server" / "arxiv_mcp_server" / "_bundle_version.py"
        ).read_text()
    )


def test_extract_mcpb_rejects_path_traversal(tmp_path):
    artifact = tmp_path / "arxiv-mcp-server-0.6.2.mcpb"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("../outside.txt", "unexpected")

    with pytest.raises(RuntimeError, match="unsafe archive member"):
        extract_mcpb(artifact, tmp_path / "unpacked")

    assert not (tmp_path / "outside.txt").exists()
