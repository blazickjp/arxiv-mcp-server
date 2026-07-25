"""Compatibility checks for the optional semantic-search dependency."""

from arxiv_mcp_server.tools import semantic_search as semantic_module


def test_get_model_uses_supported_sentence_transformer_constructor(monkeypatch):
    """The optional dependency must be instantiated with its public API."""

    calls = []

    class RecordingModel:
        def __init__(self, model_name):
            calls.append(model_name)

    monkeypatch.setattr(semantic_module, "SentenceTransformer", RecordingModel)
    semantic_module._model = None

    model = semantic_module._get_model()

    assert isinstance(model, RecordingModel)
    assert calls == [semantic_module.EMBEDDING_MODEL_NAME]
    semantic_module._model = None
