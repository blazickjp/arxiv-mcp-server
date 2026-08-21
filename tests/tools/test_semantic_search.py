"""Tests for semantic search and reindex tools."""

import json
from pathlib import Path

import pytest

from arxiv_mcp_server.tools import semantic_search as semantic_module

EDITABLE_PRO_INSTALL = 'uv pip install -e ".[pro]"'


def _assert_pro_install_hint(message: str) -> None:
    """uvx users should see a PyPI extra path; checkout install is secondary."""
    assert message
    assert "uvx" in message
    assert "arxiv-mcp-server[pro]" in message
    assert message.index("uvx") < message.index(EDITABLE_PRO_INSTALL)


@pytest.mark.asyncio
async def test_missing_pro_deps_error_mentions_uvx(monkeypatch):
    """Missing [pro] extras should not point only at a git-checkout install."""
    monkeypatch.setattr(semantic_module, "_load_dependencies", lambda: False)

    hint = semantic_module._dependency_error()
    _assert_pro_install_hint(hint)
    assert hint.count("uvx") >= 1
    assert "uv tool install" in hint

    search = await semantic_module.handle_semantic_search({"query": "transformers"})
    _assert_pro_install_hint(search[0].text)

    reindex = await semantic_module.handle_reindex({})
    payload = json.loads(reindex[0].text)
    assert payload["status"] == "error"
    _assert_pro_install_hint(payload["message"])


class DummyModel:
    """Deterministic embedding model for tests."""

    def encode(self, text, convert_to_numpy=True, normalize_embeddings=True):
        np = pytest.importorskip("numpy")
        vector = np.array(
            [
                float("transformer" in text.lower()),
                float("vision" in text.lower()),
                float("graph" in text.lower()),
            ],
            dtype=np.float32,
        )
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector


@pytest.fixture
def semantic_test_env(monkeypatch, temp_storage_path):
    """Configure semantic search module to use a temporary index and dummy model."""
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(
        semantic_module.settings,
        "_get_storage_path_from_args",
        lambda: Path(temp_storage_path),
    )
    monkeypatch.setattr(semantic_module, "np", np)
    monkeypatch.setattr(semantic_module, "SentenceTransformer", object)
    monkeypatch.setattr(semantic_module, "_get_model", lambda: DummyModel())
    semantic_module._model = None


@pytest.mark.asyncio
async def test_semantic_search_free_text(semantic_test_env):
    """Semantic text query should rank closest abstract first."""
    semantic_module._upsert_index_record(
        paper_id="2401.00001",
        title="Vision Transformers",
        abstract="transformer model for vision",
        authors=["Author 1"],
        categories=["cs.CV"],
    )
    semantic_module._upsert_index_record(
        paper_id="2401.00002",
        title="Graph Methods",
        abstract="graph neural network approach",
        authors=["Author 2"],
        categories=["cs.LG"],
    )

    response = await semantic_module.handle_semantic_search(
        {"query": "vision transformer", "max_results": 2}
    )

    payload = json.loads(response[0].text)
    assert payload["total_results"] == 2
    assert payload["papers"][0]["id"] == "2401.00001"


@pytest.mark.asyncio
async def test_semantic_search_by_paper_id(semantic_test_env):
    """similar-to-paper mode excludes the source paper from results."""
    semantic_module._upsert_index_record(
        paper_id="2402.00001",
        title="Transformer Baselines",
        abstract="transformer pretraining method",
        authors=["Author 1"],
        categories=["cs.LG"],
    )
    semantic_module._upsert_index_record(
        paper_id="2402.00002",
        title="Vision Transformer Variant",
        abstract="vision transformer architecture",
        authors=["Author 2"],
        categories=["cs.CV"],
    )

    response = await semantic_module.handle_semantic_search(
        {"paper_id": "2402.00001", "max_results": 3}
    )

    payload = json.loads(response[0].text)
    assert payload["mode"] == "similar_to_paper"
    assert all(p["id"] != "2402.00001" for p in payload["papers"])


@pytest.mark.asyncio
async def test_reindex_uses_local_markdown_ids(
    monkeypatch, semantic_test_env, temp_storage_path
):
    """Reindex should walk local markdown files and attempt indexing each ID."""
    Path(temp_storage_path, "2301.00001.md").write_text("paper", encoding="utf-8")
    Path(temp_storage_path, "2301.00002.md").write_text("paper", encoding="utf-8")

    indexed_ids = []

    def _mock_index(paper_id):
        indexed_ids.append(paper_id)
        return True

    monkeypatch.setattr(semantic_module, "index_paper_by_id", _mock_index)

    response = await semantic_module.handle_reindex({"clear_existing": True})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert set(indexed_ids) == {"2301.00001", "2301.00002"}
