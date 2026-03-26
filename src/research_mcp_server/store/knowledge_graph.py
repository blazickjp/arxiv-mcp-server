"""SQLite-backed knowledge graph for research papers.

Stores nodes (papers, concepts, methods, datasets, authors) and edges
(cites, uses_method, evaluates_on, extends, authored_by, related_to)
to capture relationships between research entities.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ..config import Settings

logger = logging.getLogger("research-mcp-server")

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall", "this",
    "that", "these", "those", "it", "its", "we", "our", "they", "their",
    "not", "no", "nor", "so", "yet", "if", "then", "than", "also", "very",
    "more", "most", "such", "each", "every", "all", "any", "some", "into",
    "about", "over", "after", "before", "between", "through", "during",
    "above", "below", "up", "down", "out", "off", "via", "using",
})

_CREATE_KG_TABLES = """
CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    label TEXT NOT NULL,
    properties TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS kg_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    PRIMARY KEY (source_id, target_id, relation),
    FOREIGN KEY (source_id) REFERENCES kg_nodes(id),
    FOREIGN KEY (target_id) REFERENCES kg_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_type ON kg_nodes(type);
"""


def _normalize_id(text: str) -> str:
    """Normalize a string into a slug suitable for a node ID."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug


class KnowledgeGraph:
    """SQLite-backed knowledge graph for research papers.

    Args:
        db_path: Path to the SQLite database. Defaults to
            {storage_path}/knowledge_graph.db.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            settings = Settings()
            db_path = settings.STORAGE_PATH / "knowledge_graph.db"
        self.db_path = db_path
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Create tables if they don't exist."""
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_CREATE_KG_TABLES)
            await db.commit()
        self._initialized = True

    # ── Node operations ──────────────────────────────────────────

    async def add_node(
        self,
        id: str,
        type: str,
        label: str,
        properties: Optional[dict[str, Any]] = None,
    ) -> None:
        """Insert or update a node.

        Args:
            id: Unique node identifier.
            type: Node type (paper, concept, method, dataset, author).
            label: Human-readable label.
            properties: Extra metadata as a dict.
        """
        await self._ensure_initialized()
        props_json = json.dumps(properties or {})
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO kg_nodes (id, type, label, properties)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    type = excluded.type,
                    label = excluded.label,
                    properties = excluded.properties""",
                (id, type, label, props_json),
            )
            await db.commit()

    async def get_node(self, id: str) -> Optional[dict[str, Any]]:
        """Get a node by ID.

        Returns:
            Node dict with id, type, label, properties, or None.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM kg_nodes WHERE id = ?", (id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _node_row_to_dict(dict(row))

    # ── Edge operations ──────────────────────────────────────────

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float = 1.0,
        properties: Optional[dict[str, Any]] = None,
    ) -> None:
        """Insert or update an edge.

        Args:
            source_id: Source node ID.
            target_id: Target node ID.
            relation: Relationship type (cites, uses_method, evaluates_on,
                extends, authored_by, related_to).
            weight: Edge weight (default 1.0).
            properties: Extra metadata as a dict.
        """
        await self._ensure_initialized()
        props_json = json.dumps(properties or {})
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO kg_edges (source_id, target_id, relation, weight, properties)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                    weight = excluded.weight,
                    properties = excluded.properties""",
                (source_id, target_id, relation, weight, props_json),
            )
            await db.commit()

    # ── Query operations ─────────────────────────────────────────

    async def get_neighbors(
        self,
        node_id: str,
        relation: Optional[str] = None,
        direction: str = "outgoing",
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Get neighboring nodes and the connecting edges.

        Args:
            node_id: Center node ID.
            relation: Filter by relation type (optional).
            direction: 'outgoing', 'incoming', or 'both'.

        Returns:
            List of (node_dict, edge_dict) tuples.
        """
        await self._ensure_initialized()
        results: list[tuple[dict[str, Any], dict[str, Any]]] = []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if direction in ("outgoing", "both"):
                sql = (
                    "SELECT n.*, e.source_id, e.target_id, e.relation, e.weight, "
                    "e.properties as edge_properties "
                    "FROM kg_edges e JOIN kg_nodes n ON e.target_id = n.id "
                    "WHERE e.source_id = ?"
                )
                params: list[Any] = [node_id]
                if relation:
                    sql += " AND e.relation = ?"
                    params.append(relation)
                cursor = await db.execute(sql, params)
                for row in await cursor.fetchall():
                    row_dict = dict(row)
                    edge = {
                        "source_id": row_dict.pop("source_id"),
                        "target_id": row_dict.pop("target_id"),
                        "relation": row_dict.pop("relation"),
                        "weight": row_dict.pop("weight"),
                        "properties": json.loads(row_dict.pop("edge_properties")),
                    }
                    node = _node_row_to_dict(row_dict)
                    results.append((node, edge))

            if direction in ("incoming", "both"):
                sql = (
                    "SELECT n.*, e.source_id, e.target_id, e.relation, e.weight, "
                    "e.properties as edge_properties "
                    "FROM kg_edges e JOIN kg_nodes n ON e.source_id = n.id "
                    "WHERE e.target_id = ?"
                )
                params = [node_id]
                if relation:
                    sql += " AND e.relation = ?"
                    params.append(relation)
                cursor = await db.execute(sql, params)
                for row in await cursor.fetchall():
                    row_dict = dict(row)
                    edge = {
                        "source_id": row_dict.pop("source_id"),
                        "target_id": row_dict.pop("target_id"),
                        "relation": row_dict.pop("relation"),
                        "weight": row_dict.pop("weight"),
                        "properties": json.loads(row_dict.pop("edge_properties")),
                    }
                    node = _node_row_to_dict(row_dict)
                    results.append((node, edge))

        return results

    async def query(
        self,
        *,
        node_type: Optional[str] = None,
        relation: Optional[str] = None,
        connected_to: Optional[str] = None,
        label_contains: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query nodes with optional filters.

        Args:
            node_type: Filter by node type.
            relation: Filter by relation (requires connected_to).
            connected_to: Find nodes connected to this node ID.
            label_contains: Case-insensitive substring match on label.
            limit: Max results.

        Returns:
            List of node dicts.
        """
        await self._ensure_initialized()

        # If connected_to is specified, join through edges
        if connected_to:
            return await self._query_connected(
                connected_to, node_type=node_type, relation=relation,
                label_contains=label_contains, limit=limit,
            )

        conditions: list[str] = []
        params: list[Any] = []

        if node_type:
            conditions.append("type = ?")
            params.append(node_type)

        if label_contains:
            conditions.append("label LIKE ?")
            params.append(f"%{label_contains}%")

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM kg_nodes WHERE {where} ORDER BY label LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [_node_row_to_dict(dict(r)) for r in rows]

    async def _query_connected(
        self,
        connected_to: str,
        *,
        node_type: Optional[str] = None,
        relation: Optional[str] = None,
        label_contains: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Find nodes connected to a given node."""
        conditions: list[str] = []
        params: list[Any] = []

        # Search both directions
        sql = (
            "SELECT DISTINCT n.* FROM kg_nodes n "
            "JOIN kg_edges e ON (n.id = e.target_id AND e.source_id = ?) "
            "OR (n.id = e.source_id AND e.target_id = ?)"
        )
        params.extend([connected_to, connected_to])

        if node_type:
            conditions.append("n.type = ?")
            params.append(node_type)

        if relation:
            conditions.append("e.relation = ?")
            params.append(relation)

        if label_contains:
            conditions.append("n.label LIKE ?")
            params.append(f"%{label_contains}%")

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY n.label LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [_node_row_to_dict(dict(r)) for r in rows]

    async def get_subgraph(
        self, center_id: str, hops: int = 2
    ) -> dict[str, Any]:
        """Get a subgraph around a center node.

        Args:
            center_id: Center node ID.
            hops: Number of hops to traverse (1-3).

        Returns:
            Dict with 'nodes' and 'edges' lists.
        """
        await self._ensure_initialized()
        hops = max(1, min(3, hops))

        visited_nodes: set[str] = set()
        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        frontier: set[str] = {center_id}

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            for _ in range(hops):
                if not frontier:
                    break

                next_frontier: set[str] = set()

                for node_id in frontier:
                    if node_id in visited_nodes:
                        continue
                    visited_nodes.add(node_id)

                    # Get the node itself
                    cursor = await db.execute(
                        "SELECT * FROM kg_nodes WHERE id = ?", (node_id,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        all_nodes.append(_node_row_to_dict(dict(row)))

                    # Get outgoing edges
                    cursor = await db.execute(
                        "SELECT * FROM kg_edges WHERE source_id = ?", (node_id,)
                    )
                    for erow in await cursor.fetchall():
                        edge = _edge_row_to_dict(dict(erow))
                        all_edges.append(edge)
                        if edge["target_id"] not in visited_nodes:
                            next_frontier.add(edge["target_id"])

                    # Get incoming edges
                    cursor = await db.execute(
                        "SELECT * FROM kg_edges WHERE target_id = ?", (node_id,)
                    )
                    for erow in await cursor.fetchall():
                        edge = _edge_row_to_dict(dict(erow))
                        all_edges.append(edge)
                        if edge["source_id"] not in visited_nodes:
                            next_frontier.add(edge["source_id"])

                frontier = next_frontier

            # Fetch remaining frontier nodes (last hop, nodes only)
            for node_id in frontier:
                if node_id not in visited_nodes:
                    visited_nodes.add(node_id)
                    cursor = await db.execute(
                        "SELECT * FROM kg_nodes WHERE id = ?", (node_id,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        all_nodes.append(_node_row_to_dict(dict(row)))

        # Deduplicate edges
        seen_edges: set[tuple[str, str, str]] = set()
        unique_edges: list[dict[str, Any]] = []
        for edge in all_edges:
            key = (edge["source_id"], edge["target_id"], edge["relation"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(edge)

        return {"nodes": all_nodes, "edges": unique_edges}

    # ── Auto-extraction ──────────────────────────────────────────

    async def extract_from_paper(self, paper: dict[str, Any]) -> None:
        """Auto-extract concepts, methods, datasets, and authors from a paper.

        Creates nodes and edges in the knowledge graph based on the paper's
        title, abstract, and author list.

        Args:
            paper: Paper dict with at least 'id' and 'title'. Optional:
                'abstract', 'authors', 'categories', 'published_date', 'url'.
        """
        paper_id = paper["id"]
        title = paper["title"]
        abstract = paper.get("abstract", "") or ""
        authors = paper.get("authors", [])

        # Add paper node
        paper_props: dict[str, Any] = {}
        if paper.get("categories"):
            paper_props["categories"] = paper["categories"]
        if paper.get("published_date"):
            paper_props["published_date"] = paper["published_date"]
        if paper.get("url"):
            paper_props["url"] = paper["url"]

        await self.add_node(paper_id, "paper", title, paper_props)

        # Extract and add concepts from title
        concepts = _extract_concepts(title)
        for concept in concepts:
            concept_id = f"concept-{_normalize_id(concept)}"
            await self.add_node(concept_id, "concept", concept)
            await self.add_edge(paper_id, concept_id, "related_to")

        # Extract methods from abstract
        methods = _extract_methods(abstract)
        for method in methods:
            method_id = f"method-{_normalize_id(method)}"
            await self.add_node(method_id, "method", method)
            await self.add_edge(paper_id, method_id, "uses_method")

        # Extract datasets from abstract
        datasets = _extract_datasets(abstract)
        for dataset in datasets:
            dataset_id = f"dataset-{_normalize_id(dataset)}"
            await self.add_node(dataset_id, "dataset", dataset)
            await self.add_edge(paper_id, dataset_id, "evaluates_on")

        # Add author nodes
        for author_name in authors:
            author_id = f"author-{_normalize_id(author_name)}"
            await self.add_node(author_id, "author", author_name)
            await self.add_edge(paper_id, author_id, "authored_by")

    # ── Statistics ────────────────────────────────────────────────

    async def get_stats(self) -> dict[str, Any]:
        """Get knowledge graph statistics.

        Returns:
            Dict with counts by node type and relation type.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            # Total nodes
            cursor = await db.execute("SELECT COUNT(*) FROM kg_nodes")
            total_nodes = (await cursor.fetchone())[0]

            # Total edges
            cursor = await db.execute("SELECT COUNT(*) FROM kg_edges")
            total_edges = (await cursor.fetchone())[0]

            # Nodes by type
            cursor = await db.execute(
                "SELECT type, COUNT(*) FROM kg_nodes GROUP BY type ORDER BY COUNT(*) DESC"
            )
            nodes_by_type = {r[0]: r[1] for r in await cursor.fetchall()}

            # Edges by relation
            cursor = await db.execute(
                "SELECT relation, COUNT(*) FROM kg_edges GROUP BY relation ORDER BY COUNT(*) DESC"
            )
            edges_by_relation = {r[0]: r[1] for r in await cursor.fetchall()}

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_type": nodes_by_type,
            "edges_by_relation": edges_by_relation,
        }


# ── Helpers ──────────────────────────────────────────────────────


def _node_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a node row to a dict with parsed properties."""
    result = dict(row)
    props = result.get("properties")
    if isinstance(props, str):
        try:
            result["properties"] = json.loads(props)
        except json.JSONDecodeError:
            result["properties"] = {}
    return result


def _edge_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Convert an edge row to a dict with parsed properties."""
    result = dict(row)
    props = result.get("properties")
    if isinstance(props, str):
        try:
            result["properties"] = json.loads(props)
        except json.JSONDecodeError:
            result["properties"] = {}
    return result


def _extract_concepts(title: str) -> list[str]:
    """Extract significant noun phrases (2-3 word phrases) from a title.

    Skips stopwords and single-character tokens. Returns unique phrases.
    """
    # Clean title
    words = re.findall(r"[a-zA-Z0-9]+(?:[-][a-zA-Z0-9]+)*", title)
    # Filter stopwords and short words
    significant = [
        w for w in words
        if w.lower() not in _STOPWORDS and len(w) > 1
    ]

    concepts: list[str] = []
    seen: set[str] = set()

    # Extract 2-word phrases
    for i in range(len(significant) - 1):
        phrase = f"{significant[i]} {significant[i + 1]}"
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            concepts.append(phrase)

    # Extract 3-word phrases
    for i in range(len(significant) - 2):
        phrase = f"{significant[i]} {significant[i + 1]} {significant[i + 2]}"
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            concepts.append(phrase)

    return concepts


def _extract_methods(abstract: str) -> list[str]:
    """Extract method names from abstract using regex patterns.

    Looks for patterns like 'we propose X', 'we use X', 'we employ X',
    'we introduce X', 'we present X'.
    """
    if not abstract:
        return []

    patterns = [
        r"[Ww]e\s+(?:propose|introduce|present|develop)\s+(?:a\s+|an\s+)?(.+?)(?:\.|,|;|\s+that|\s+which|\s+to\b)",
        r"[Ww]e\s+(?:use|employ|apply|leverage|utilize)\s+(?:a\s+|an\s+|the\s+)?(.+?)(?:\.|,|;|\s+to\b|\s+for\b)",
        r"(?:novel|new)\s+(?:method|approach|framework|model|technique|architecture)\s+(?:called|named|termed)\s+(.+?)(?:\.|,|;|\s)",
    ]

    methods: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        for match in re.finditer(pattern, abstract):
            method = match.group(1).strip()
            # Clean up: take first few words (method names are usually short)
            words = method.split()[:5]
            method = " ".join(words).rstrip(".,;:")
            key = method.lower()
            if key not in seen and len(method) > 2:
                seen.add(key)
                methods.append(method)

    return methods


def _extract_datasets(abstract: str) -> list[str]:
    """Extract dataset names from abstract using regex patterns.

    Looks for patterns like 'on X dataset', 'on the X benchmark',
    'evaluated on X'.
    """
    if not abstract:
        return []

    patterns = [
        r"(?:on|using)\s+(?:the\s+)?(.+?)\s+(?:dataset|benchmark|corpus|corpora)",
        r"(?:evaluated|tested|experiments?)\s+on\s+(?:the\s+)?(.+?)(?:\.|,|;|\s+and\b|\s+dataset)",
    ]

    datasets: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        for match in re.finditer(pattern, abstract, re.IGNORECASE):
            dataset = match.group(1).strip()
            # Clean up: take first few words
            words = dataset.split()[:4]
            dataset = " ".join(words).rstrip(".,;:")
            key = dataset.lower()
            if key not in seen and len(dataset) > 1:
                seen.add(key)
                datasets.append(dataset)

    return datasets
