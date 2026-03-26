"""Research lineage tool — build a DAG of intellectual influence around a paper."""

import json
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import mcp.types as types

from ..clients.s2_client import S2Client, DEFAULT_CITATION_FIELDS
from ..utils.rate_limiter import s2_limiter

logger = logging.getLogger("research-mcp-server")


research_lineage_tool = types.Tool(
    name="lineage",
    description="""Build a directed acyclic graph (DAG) of intellectual influence around an arXiv paper.

Traces the lineage of ideas both backward (what the paper builds on) and forward
(what it influenced), identifying:
- **Foundations**: papers cited by 3+ of the root's references (shared intellectual roots)
- **Methodological ancestors**: highest-cited references (established methods the root builds on)
- **Key descendants**: citing papers with highest own citation counts (most impactful follow-up work)
- **Research threads**: groups of descendants clustered by shared title keywords

Returns a full DAG (nodes + edges) plus categorized analysis.

EXAMPLES:
- Full lineage: paper_id="2401.12345"
- Ancestors only, deeper: paper_id="2401.12345", direction="ancestors", depth=3
- Broad descendant scan: paper_id="2401.12345", direction="descendants", max_per_level=30""",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "arXiv paper ID (e.g., '2401.12345'). Do not include version suffix.",
            },
            "depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "How many hops to trace from the root paper. Default: 2.",
            },
            "max_per_level": {
                "type": "integer",
                "minimum": 5,
                "maximum": 50,
                "description": (
                    "Maximum papers to fetch per level of the DAG. "
                    "Higher values give broader coverage but use more API calls. Default: 15."
                ),
            },
            "direction": {
                "type": "string",
                "enum": ["ancestors", "descendants", "both"],
                "description": (
                    "Direction to trace. 'ancestors' = what it builds on (references), "
                    "'descendants' = what it influenced (citations), 'both' = full lineage. "
                    "Default: 'both'."
                ),
            },
        },
        "required": ["paper_id"],
    },
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TITLE_STOPWORDS: Set[str] = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is",
    "are", "was", "were", "with", "from", "by", "at", "as", "its", "it",
    "this", "that", "these", "those", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "would", "shall", "should", "may",
    "might", "can", "could", "not", "no", "but", "if", "then", "than",
    "so", "very", "just", "about", "up", "out", "into", "over", "after",
    "before", "between", "under", "above", "below", "each", "every", "all",
    "both", "few", "more", "most", "other", "some", "such", "only", "own",
    "same", "also", "how", "what", "which", "who", "whom", "why", "where",
    "when", "while", "through", "during", "via", "using", "based", "towards",
    "toward", "new", "novel", "approach", "method", "methods", "paper",
}


def _extract_keywords(title: str) -> Set[str]:
    """Extract meaningful keywords from a paper title."""
    words = title.lower().split()
    cleaned: Set[str] = set()
    for w in words:
        w = "".join(c for c in w if c.isalnum() or c == "-")
        if w and w not in _TITLE_STOPWORDS and len(w) > 2:
            cleaned.add(w)
    return cleaned


def _paper_node(paper: Dict[str, Any], role: str) -> Dict[str, Any]:
    """Build a node dict from a paper response."""
    return {
        "title": paper.get("title", "Unknown"),
        "year": paper.get("year"),
        "citations": paper.get("citationCount", 0) or 0,
        "role": role,
    }


async def _fetch_references_for_s2_id(
    client: S2Client,
    s2_paper_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch references for a paper using its S2 paper ID.

    Returns lightweight paper dicts (paperId, title, year, citationCount).
    """
    try:
        params = {
            "fields": DEFAULT_CITATION_FIELDS,
            "limit": min(limit, 1000),
        }
        result = await client._request(
            "GET", f"/paper/{s2_paper_id}/references", params=params
        )
        return [
            item["citedPaper"]
            for item in result.get("data", [])
            if item.get("citedPaper", {}).get("paperId")
        ]
    except Exception as e:
        logger.debug(f"Failed to fetch references for {s2_paper_id}: {e}")
        return []


async def _fetch_citations_for_s2_id(
    client: S2Client,
    s2_paper_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch citations for a paper using its S2 paper ID.

    Returns lightweight paper dicts.
    """
    try:
        params = {
            "fields": DEFAULT_CITATION_FIELDS,
            "limit": min(limit, 1000),
        }
        result = await client._request(
            "GET", f"/paper/{s2_paper_id}/citations", params=params
        )
        return [
            item["citingPaper"]
            for item in result.get("data", [])
            if item.get("citingPaper", {}).get("paperId")
        ]
    except Exception as e:
        logger.debug(f"Failed to fetch citations for {s2_paper_id}: {e}")
        return []


def _group_by_keywords(
    papers: List[Tuple[str, Dict[str, Any]]],
    min_group_size: int = 2,
    max_groups: int = 10,
) -> List[Dict[str, Any]]:
    """Group papers into research threads by shared title keywords.

    Args:
        papers: List of (paper_id, node_dict) tuples.
        min_group_size: Minimum papers to form a thread.
        max_groups: Maximum threads to return.

    Returns:
        List of thread dicts with label, paper_ids, and size.
    """
    if not papers:
        return []

    # Build keyword -> paper_ids mapping
    keyword_to_papers: Dict[str, List[str]] = defaultdict(list)
    paper_keywords: Dict[str, Set[str]] = {}

    for pid, node in papers:
        kws = _extract_keywords(node.get("title", ""))
        paper_keywords[pid] = kws
        for kw in kws:
            keyword_to_papers[kw].append(pid)

    # Find keyword pairs that co-occur in multiple papers (more specific threads)
    pair_counter: Counter = Counter()
    for pid, kws in paper_keywords.items():
        kw_list = sorted(kws)
        for i in range(len(kw_list)):
            for j in range(i + 1, len(kw_list)):
                pair_counter[(kw_list[i], kw_list[j])] += 1

    # Build threads from top keyword pairs
    assigned: Set[str] = set()
    threads: List[Dict[str, Any]] = []

    for (kw1, kw2), count in pair_counter.most_common(max_groups * 3):
        if count < min_group_size:
            break
        # Papers containing both keywords
        pids_with_pair = [
            pid for pid, kws in paper_keywords.items()
            if kw1 in kws and kw2 in kws and pid not in assigned
        ]
        if len(pids_with_pair) < min_group_size:
            continue

        for pid in pids_with_pair:
            assigned.add(pid)

        threads.append({
            "label": f"{kw1}, {kw2}",
            "paper_ids": pids_with_pair,
            "size": len(pids_with_pair),
        })

        if len(threads) >= max_groups:
            break

    # Also try single-keyword threads for unassigned papers
    remaining = [(pid, node) for pid, node in papers if pid not in assigned]
    if remaining:
        single_kw_counter: Counter = Counter()
        for pid, node in remaining:
            for kw in paper_keywords.get(pid, set()):
                single_kw_counter[kw] += 1

        for kw, count in single_kw_counter.most_common(max_groups - len(threads)):
            if count < min_group_size:
                break
            pids = [
                pid for pid, _ in remaining
                if kw in paper_keywords.get(pid, set()) and pid not in assigned
            ]
            if len(pids) < min_group_size:
                continue
            for pid in pids:
                assigned.add(pid)
            threads.append({
                "label": kw,
                "paper_ids": pids,
                "size": len(pids),
            })
            if len(threads) >= max_groups:
                break

    threads.sort(key=lambda t: t["size"], reverse=True)
    return threads


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_research_lineage(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle research lineage DAG construction.

    Builds a multi-hop directed graph of intellectual influence around a paper,
    tracing ancestors (references) and/or descendants (citations) up to the
    requested depth.
    """
    try:
        paper_id: str = arguments["paper_id"]
        depth: int = max(1, min(arguments.get("depth", 2), 3))
        max_per_level: int = max(5, min(arguments.get("max_per_level", 15), 50))
        direction: str = arguments.get("direction", "both")
        if direction not in ("ancestors", "descendants", "both"):
            direction = "both"

        client = S2Client()
        degradation_notes: List[str] = []

        # ── Step 1: Fetch root paper ──────────────────────────────
        try:
            root_paper = await client.get_paper(paper_id)
        except ValueError as e:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "paper_not_found",
                            "message": str(e),
                            "suggestion": (
                                "Ensure the arXiv ID is correct and does not "
                                "include a version suffix (e.g., use '2401.12345' "
                                "not '2401.12345v2')."
                            ),
                        },
                        indent=2,
                    ),
                )
            ]

        root_s2_id: str = root_paper.get("paperId", "")
        nodes: Dict[str, Dict[str, Any]] = {
            root_s2_id: _paper_node(root_paper, "root"),
        }
        edges: List[Tuple[str, str, str]] = []

        # Track which IDs we have already expanded to avoid cycles
        expanded_ancestors: Set[str] = set()
        expanded_descendants: Set[str] = set()

        # Counter for detecting foundations (papers cited by multiple refs)
        ancestor_ref_counter: Counter = Counter()

        # ── Step 2a: Trace ancestors (references) ─────────────────
        if direction in ("ancestors", "both"):
            # Level 1: root's references
            await s2_limiter.wait()
            try:
                level1_refs = await client.get_references(
                    paper_id, limit=max_per_level
                )
            except Exception as e:
                logger.warning(f"Failed to fetch root references: {e}")
                level1_refs = []
                degradation_notes.append(f"Root references fetch failed: {e}")

            for ref in level1_refs:
                ref_id = ref.get("paperId", "")
                if not ref_id:
                    continue
                if ref_id not in nodes:
                    nodes[ref_id] = _paper_node(ref, "ancestor")
                edges.append((root_s2_id, ref_id, "cites"))

            expanded_ancestors.add(root_s2_id)

            # Deeper levels
            current_frontier: List[str] = [
                ref.get("paperId", "")
                for ref in level1_refs
                if ref.get("paperId")
            ]

            for current_depth in range(2, depth + 1):
                next_frontier: List[str] = []
                for frontier_id in current_frontier[:max_per_level]:
                    if frontier_id in expanded_ancestors:
                        continue
                    expanded_ancestors.add(frontier_id)

                    await s2_limiter.wait()
                    try:
                        deeper_refs = await _fetch_references_for_s2_id(
                            client, frontier_id, limit=max_per_level
                        )
                    except Exception as e:
                        logger.debug(
                            f"Rate limited or failed for ancestor {frontier_id}: {e}"
                        )
                        degradation_notes.append(
                            f"Skipped ancestor expansion for {frontier_id}: {e}"
                        )
                        continue

                    for dref in deeper_refs:
                        dref_id = dref.get("paperId", "")
                        if not dref_id:
                            continue
                        # Track how many level-1 refs cite this paper
                        if current_depth == 2 and frontier_id != root_s2_id:
                            ancestor_ref_counter[dref_id] += 1
                        if dref_id not in nodes:
                            nodes[dref_id] = _paper_node(dref, "ancestor")
                        edges.append((frontier_id, dref_id, "cites"))
                        next_frontier.append(dref_id)

                current_frontier = next_frontier

        # ── Step 2b: Trace descendants (citations) ────────────────
        if direction in ("descendants", "both"):
            # Level 1: papers citing root
            await s2_limiter.wait()
            try:
                level1_cits = await client.get_citations(
                    paper_id, limit=max_per_level
                )
            except Exception as e:
                logger.warning(f"Failed to fetch root citations: {e}")
                level1_cits = []
                degradation_notes.append(f"Root citations fetch failed: {e}")

            for cit in level1_cits:
                cit_id = cit.get("paperId", "")
                if not cit_id:
                    continue
                if cit_id not in nodes:
                    nodes[cit_id] = _paper_node(cit, "descendant")
                edges.append((cit_id, root_s2_id, "cites"))

            expanded_descendants.add(root_s2_id)

            # Deeper levels
            current_frontier = [
                cit.get("paperId", "")
                for cit in level1_cits
                if cit.get("paperId")
            ]

            for current_depth in range(2, depth + 1):
                next_frontier = []
                for frontier_id in current_frontier[:max_per_level]:
                    if frontier_id in expanded_descendants:
                        continue
                    expanded_descendants.add(frontier_id)

                    await s2_limiter.wait()
                    try:
                        deeper_cits = await _fetch_citations_for_s2_id(
                            client, frontier_id, limit=max_per_level
                        )
                    except Exception as e:
                        logger.debug(
                            f"Rate limited or failed for descendant {frontier_id}: {e}"
                        )
                        degradation_notes.append(
                            f"Skipped descendant expansion for {frontier_id}: {e}"
                        )
                        continue

                    for dcit in deeper_cits:
                        dcit_id = dcit.get("paperId", "")
                        if not dcit_id:
                            continue
                        if dcit_id not in nodes:
                            nodes[dcit_id] = _paper_node(dcit, "descendant")
                        edges.append((dcit_id, frontier_id, "cites"))
                        next_frontier.append(dcit_id)

                current_frontier = next_frontier

        # ── Step 3: Identify foundations ───────────────────────────
        # Papers cited by 3+ of the root's direct references.
        # Requires depth >= 2 (we need the refs-of-refs to count shared targets).
        # ancestor_ref_counter is populated during the depth-2+ ancestor traversal.

        foundations: List[Dict[str, Any]] = []
        for fid, count in ancestor_ref_counter.most_common(20):
            if count < 3:
                break
            node = nodes.get(fid)
            if node:
                # Upgrade role
                nodes[fid]["role"] = "foundation"
                foundations.append({
                    "paper_id": fid,
                    "title": node["title"],
                    "year": node["year"],
                    "citations": node["citations"],
                    "referenced_by_n_ancestors": count,
                })

        # ── Step 4: Methodological ancestors ──────────────────────
        # Direct references of root, sorted by citation count
        direct_ref_ids: Set[str] = set()
        for src, tgt, rel in edges:
            if src == root_s2_id and rel == "cites":
                direct_ref_ids.add(tgt)

        methodological_ancestors: List[Dict[str, Any]] = sorted(
            [
                {
                    "paper_id": pid,
                    "title": nodes[pid]["title"],
                    "year": nodes[pid]["year"],
                    "citations": nodes[pid]["citations"],
                }
                for pid in direct_ref_ids
                if pid in nodes
            ],
            key=lambda p: p["citations"],
            reverse=True,
        )[:10]

        # ── Step 5: Key descendants ───────────────────────────────
        # Direct citers of root, sorted by their own citation count
        direct_citer_ids: Set[str] = set()
        for src, tgt, rel in edges:
            if tgt == root_s2_id and rel == "cites":
                direct_citer_ids.add(src)

        key_descendants: List[Dict[str, Any]] = sorted(
            [
                {
                    "paper_id": pid,
                    "title": nodes[pid]["title"],
                    "year": nodes[pid]["year"],
                    "citations": nodes[pid]["citations"],
                }
                for pid in direct_citer_ids
                if pid in nodes
            ],
            key=lambda p: p["citations"],
            reverse=True,
        )[:10]

        # ── Step 6: Research threads ──────────────────────────────
        descendant_papers = [
            (pid, node)
            for pid, node in nodes.items()
            if node["role"] == "descendant"
        ]
        research_threads = _group_by_keywords(descendant_papers)

        # ── Step 7: Deduplicate edges ─────────────────────────────
        seen_edges: Set[Tuple[str, str, str]] = set()
        serialized_edges: List[List[str]] = []
        for edge_tuple in edges:
            if edge_tuple not in seen_edges:
                seen_edges.add(edge_tuple)
                serialized_edges.append(list(edge_tuple))

        # ── Build result ──────────────────────────────────────────
        result: Dict[str, Any] = {
            "root": {
                "paper_id": root_s2_id,
                "arxiv_id": paper_id,
                "title": root_paper.get("title", "Unknown"),
                "year": root_paper.get("year"),
                "citations": root_paper.get("citationCount", 0),
            },
            "nodes": nodes,
            "edges": serialized_edges,
            "foundations": foundations,
            "methodological_ancestors": methodological_ancestors,
            "key_descendants": key_descendants,
            "research_threads": research_threads,
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(serialized_edges),
                "depth_reached": depth,
                "direction": direction,
            },
        }

        if degradation_notes:
            result["notes"] = degradation_notes

        return [
            types.TextContent(type="text", text=json.dumps(result, indent=2))
        ]

    except ValueError as e:
        logger.error(f"Research lineage error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    except Exception as e:
        logger.error(f"Unexpected research lineage error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
