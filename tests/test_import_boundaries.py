"""Regression tests for optional dependency import boundaries."""

import json
import subprocess
import sys


HEAVY_MODULES = {"fitz", "pymupdf4llm", "numpy", "sentence_transformers", "torch"}


def _imported_after(statement: str) -> set[str]:
    script = (
        f"{statement}\n"
        "import json, sys\n"
        f"print(json.dumps(sorted(set(sys.modules) & {HEAVY_MODULES!r})))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_importing_server_does_not_load_optional_ml_or_pdf_stacks():
    assert _imported_after("import arxiv_mcp_server.server") == set()


def test_importing_resources_package_is_lightweight():
    assert _imported_after("import arxiv_mcp_server.resources") == set()
