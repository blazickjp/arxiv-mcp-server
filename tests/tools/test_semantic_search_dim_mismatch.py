import json
from pathlib import Path

import pytest

from arxiv_mcp_server.tools import semantic_search as semantic_module

np = pytest.importorskip("numpy")


class DummyModelDim4:
    def encode(self, text, convert_to_numpy=True, normalize_embeddings=True):
        v = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        norm = np.linalg.norm(v)
        if norm > 0:
            v = v / norm
        return v


@pytest.fixture
def semantic_test_env(monkeypatch, temp_storage_path):
    """Configure semantic search module to use a temporary index and dummy model."""
    monkeypatch.setattr(
        semantic_module.settings,
        "_get_storage_path_from_args",
        lambda: Path(temp_storage_path),
    )
    monkeypatch.setattr(semantic_module, "np", np)
    monkeypatch.setattr(semantic_module, "SentenceTransformer", object)
    monkeypatch.setattr(semantic_module, "_get_model", lambda: DummyModelDim4())
    semantic_module._model = None


def test_load_vectors_skips_mismatched_dim(semantic_test_env):
    # Create a DB record with embedding_dim 3 (3 floats)
    emb = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    with semantic_module._connect() as conn:
        conn.execute("DELETE FROM semantic_index")
        conn.execute(
            """
            INSERT INTO semantic_index (
                paper_id, title, abstract, authors_json, categories_json,
                published, embedding, embedding_dim, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test.00001",
                "Title",
                "Abstract",
                json.dumps(["Author"]),
                json.dumps(["cs.LG"]),
                "",
                emb.tobytes(),
                int(emb.shape[0]),
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()

    vecs = semantic_module._load_vectors()
    assert len(vecs) == 0
