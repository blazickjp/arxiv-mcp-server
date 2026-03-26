"""Semantic tool discovery for the research MCP server.

Uses sentence-transformers embeddings to recommend the most relevant tools
for a natural-language query, reducing token usage by surfacing only the
tools that matter.
"""

import json
import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
import numpy as np

from ..config import Settings

logger = logging.getLogger("research-mcp-server")

# Module-level registry populated at server startup
_ALL_TOOLS: List[types.Tool] = []


def register_all_tools(tools: List[types.Tool]) -> None:
    """Register the full tool list so suggest_tools can index them.

    Called once at server startup after all tools are known.
    """
    global _ALL_TOOLS
    _ALL_TOOLS = list(tools)
    # Invalidate any cached index so it rebuilds on next query
    global _index
    _index = None


class ToolIndex:
    """Embedding-based index over MCP tool definitions."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model: Any = None
        self._embeddings: Optional[np.ndarray] = None
        self._tool_dicts: List[Dict[str, Any]] = []

    def _load_model(self) -> None:
        """Lazy-load the sentence-transformers model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)

    @staticmethod
    def _build_tool_text(tool: Dict[str, Any]) -> str:
        """Concatenate name + description + parameter descriptions into indexable text."""
        parts = [tool.get("name", ""), tool.get("description", "")]
        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {})
        for param_name, param_def in properties.items():
            param_desc = param_def.get("description", "")
            parts.append(f"{param_name}: {param_desc}")
        return " ".join(parts)

    def _persistence_path(self) -> Path:
        """Return the path for the persisted index pickle."""
        return Settings().STORAGE_PATH / "tool_index.pkl"

    def build(self, tools: List[Dict[str, Any]]) -> None:
        """Encode all tools and persist the index to disk."""
        self._load_model()
        self._tool_dicts = tools
        texts = [self._build_tool_text(t) for t in tools]
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        self._embeddings = np.array(embeddings, dtype=np.float32)

        pkl_path = self._persistence_path()
        pkl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pkl_path, "wb") as f:
            pickle.dump(
                {"tool_dicts": self._tool_dicts, "embeddings": self._embeddings},
                f,
            )
        logger.info("Tool index built and saved to %s (%d tools)", pkl_path, len(tools))

    def load(self) -> bool:
        """Load a previously persisted index. Returns True on success."""
        pkl_path = self._persistence_path()
        if not pkl_path.exists():
            return False
        try:
            with open(pkl_path, "rb") as f:
                data = pickle.load(f)
            self._tool_dicts = data["tool_dicts"]
            self._embeddings = data["embeddings"]
            return True
        except Exception as exc:
            logger.warning("Failed to load tool index: %s", exc)
            return False

    def query(self, text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Find the top-k most relevant tools for a query string."""
        if self._embeddings is None or len(self._tool_dicts) == 0:
            return []

        self._load_model()
        q_emb = self._model.encode([text], normalize_embeddings=True)
        q_vec = np.array(q_emb, dtype=np.float32)[0]

        # Cosine similarity (embeddings are already L2-normalized)
        scores = self._embeddings @ q_vec
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            tool = self._tool_dicts[int(idx)]
            desc = tool.get("description", "")
            results.append(
                {
                    "tool_name": tool["name"],
                    "description": desc[:200] if len(desc) > 200 else desc,
                    "score": round(float(scores[int(idx)]), 4),
                }
            )
        return results


# Singleton index
_index: Optional[ToolIndex] = None


def _ensure_index() -> ToolIndex:
    """Return the singleton ToolIndex, building it if necessary."""
    global _index
    if _index is not None and _index._embeddings is not None:
        return _index

    _index = ToolIndex()

    # Try loading from disk first
    if _index.load():
        return _index

    # Build from registered tools
    if not _ALL_TOOLS:
        logger.warning("suggest_tools called but no tools registered")
        return _index

    tool_dicts = []
    for t in _ALL_TOOLS:
        tool_dicts.append(
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema or {},
            }
        )
    _index.build(tool_dicts)
    return _index


# ---------------------------------------------------------------------------
# MCP Tool definition
# ---------------------------------------------------------------------------

suggest_tools_tool = types.Tool(
    name="suggest_tools",
    description=(
        "Recommend the most relevant tools for a natural-language research query. "
        "Uses semantic embeddings to match your intent against all available tool "
        "descriptions, so you can discover the right tool without reading every schema. "
        "Returns ranked suggestions with similarity scores and an estimate of token savings."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language description of what you want to do, "
                    "e.g. 'find papers about transformers in NLP' or "
                    "'export bibliography references as BibTeX'."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Number of tool suggestions to return (default 5, max 10).",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    },
)


async def handle_suggest_tools(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle the suggest_tools tool call."""
    query = arguments.get("query", "")
    top_k = min(arguments.get("top_k", 5), 10)

    if not query.strip():
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"error": "query must be a non-empty string"}, indent=2),
            )
        ]

    index = _ensure_index()
    suggestions = index.query(query, top_k=top_k)

    # Token savings estimate: compare full schema chars vs selected schema chars
    all_tools_chars = 0
    selected_chars = 0
    selected_names = {s["tool_name"] for s in suggestions}

    for t in _ALL_TOOLS:
        schema_text = json.dumps(
            {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema or {}}
        )
        chars = len(schema_text)
        all_tools_chars += chars
        if t.name in selected_names:
            selected_chars += chars

    if all_tools_chars > 0:
        reduction_percent = round((1 - selected_chars / all_tools_chars) * 100, 1)
    else:
        reduction_percent = 0.0

    result = {
        "query": query,
        "suggestions": suggestions,
        "token_savings": {
            "all_tools_chars": all_tools_chars,
            "selected_tools_chars": selected_chars,
            "reduction_percent": reduction_percent,
        },
    }

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
