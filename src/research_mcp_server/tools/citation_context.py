"""Citation context analysis tool — structural analysis of a paper's citation landscape."""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import mcp.types as types

from ..clients.s2_client import S2Client, DEFAULT_CITATION_FIELDS
from ..utils.rate_limiter import s2_limiter

logger = logging.getLogger("research-mcp-server")


citation_context_tool = types.Tool(
    name="arxiv_citation_context",
    description="""Analyze WHERE an arXiv paper sits in the research landscape. Use when you need deeper insight than a simple citation list (use arxiv_citation_graph for that). Returns: foundational papers (most-cited refs), bridge papers (shared across citers), citation clusters (groups sharing refs), temporal impact curve, and citation velocity.

Makes many Semantic Scholar API calls -- slower than arxiv_citation_graph. Omit version suffix from IDs.

Examples: paper_id="2401.12345" | paper_id="1706.03762", max_citations=100""",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "arXiv paper ID (e.g., '2401.12345'). Do not include version suffix.",
            },
            "max_citations": {
                "type": "integer",
                "minimum": 10,
                "maximum": 200,
                "description": "Maximum number of citations/references to analyze. Default: 50.",
            },
        },
        "required": ["paper_id"],
    },
)


async def _fetch_references_for_s2_paper(
    client: S2Client,
    s2_paper_id: str,
    limit: int = 100,
) -> List[str]:
    """Fetch reference paper IDs for a paper using its S2 paper ID directly.

    Returns only the paper IDs (lightweight) to minimize API response size.
    """
    try:
        params = {
            "fields": "paperId",
            "limit": min(limit, 1000),
        }
        result = await client._request(
            "GET", f"/paper/{s2_paper_id}/references", params=params
        )
        return [
            item["citedPaper"]["paperId"]
            for item in result.get("data", [])
            if item.get("citedPaper", {}).get("paperId")
        ]
    except Exception as e:
        logger.debug(f"Failed to fetch references for {s2_paper_id}: {e}")
        return []


def _extract_title_keywords(title: str) -> Set[str]:
    """Extract meaningful keywords from a paper title."""
    stopwords = {
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
    words = title.lower().split()
    # Remove non-alphanumeric chars from each word
    cleaned = []
    for w in words:
        w = "".join(c for c in w if c.isalnum() or c == "-")
        if w and w not in stopwords and len(w) > 2:
            cleaned.append(w)
    return set(cleaned)


def _compute_jaccard(set_a: Set[str], set_b: Set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _find_clusters(
    papers: List[Dict[str, Any]],
    reference_sets: Dict[str, Set[str]],
    threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """Group papers into clusters based on shared references (Jaccard similarity).

    Uses a simple greedy clustering: iterate papers, assign to the first cluster
    with avg similarity > threshold, or create a new cluster.
    """
    clusters: List[List[Dict[str, Any]]] = []
    cluster_ref_sets: List[List[Set[str]]] = []

    for paper in papers:
        pid = paper.get("paperId", "")
        refs = reference_sets.get(pid, set())

        assigned = False
        for i, cluster_refs in enumerate(cluster_ref_sets):
            # Compute average Jaccard with cluster members
            similarities = [_compute_jaccard(refs, cr) for cr in cluster_refs]
            avg_sim = sum(similarities) / len(similarities) if similarities else 0.0
            if avg_sim >= threshold:
                clusters[i].append(paper)
                cluster_ref_sets[i].append(refs)
                assigned = True
                break

        if not assigned:
            clusters.append([paper])
            cluster_ref_sets.append([refs])

    # Build cluster output — only include clusters with 2+ papers
    result = []
    for i, cluster_papers in enumerate(clusters):
        if len(cluster_papers) < 2:
            continue

        # Find common references across cluster
        all_refs_in_cluster = [
            reference_sets.get(p.get("paperId", ""), set()) for p in cluster_papers
        ]
        if all_refs_in_cluster:
            common_refs = set.intersection(*all_refs_in_cluster) if len(all_refs_in_cluster) > 1 else set()
        else:
            common_refs = set()

        # Label by most common title keywords
        keyword_counter: Counter = Counter()
        for p in cluster_papers:
            title = p.get("title", "")
            keyword_counter.update(_extract_title_keywords(title))
        top_keywords = [kw for kw, _ in keyword_counter.most_common(5)]
        label = ", ".join(top_keywords) if top_keywords else "unlabeled"

        result.append({
            "label": label,
            "size": len(cluster_papers),
            "papers": [
                {"title": p.get("title", ""), "year": p.get("year")}
                for p in cluster_papers
            ],
            "common_references_count": len(common_refs),
        })

    # Sort by cluster size descending
    result.sort(key=lambda c: c["size"], reverse=True)
    return result


async def handle_citation_context(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle citation context analysis requests.

    Fetches citation and reference data, then performs structural analysis
    to reveal foundational papers, bridge papers, citation clusters, and
    temporal impact patterns.
    """
    try:
        paper_id = arguments["paper_id"]
        max_citations = arguments.get("max_citations", 50)
        max_citations = max(10, min(max_citations, 200))

        client = S2Client()
        degradation_notes: List[str] = []

        # Step 1: Fetch root paper
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

        root_title = root_paper.get("title", "Unknown")
        root_year = root_paper.get("year")
        total_citation_count = root_paper.get("citationCount", 0)
        total_reference_count = root_paper.get("referenceCount", 0)

        # Step 2: Fetch citations and references
        await s2_limiter.wait()
        try:
            citations = await client.get_citations(paper_id, limit=max_citations)
        except Exception as e:
            logger.warning(f"Failed to fetch citations: {e}")
            citations = []
            degradation_notes.append(f"Citations fetch failed: {e}")

        await s2_limiter.wait()
        try:
            references = await client.get_references(paper_id, limit=max_citations)
        except Exception as e:
            logger.warning(f"Failed to fetch references: {e}")
            references = []
            degradation_notes.append(f"References fetch failed: {e}")

        # -- Analysis (a): Foundational Papers --
        # Sort references by citation count descending
        sorted_refs = sorted(
            references,
            key=lambda p: p.get("citationCount", 0) or 0,
            reverse=True,
        )
        foundational_papers = []
        for ref in sorted_refs[:10]:
            foundational_papers.append({
                "title": ref.get("title", ""),
                "year": ref.get("year"),
                "citations": ref.get("citationCount", 0),
                "paperId": ref.get("paperId"),
            })

        # -- Analysis (d): Temporal Impact --
        temporal_impact: Dict[str, int] = defaultdict(int)
        for cit in citations:
            year = cit.get("year")
            if year:
                temporal_impact[str(year)] += 1
        # Sort by year
        temporal_impact = dict(sorted(temporal_impact.items()))

        # -- Analysis (e): Citation Velocity --
        current_year = datetime.now().year
        years_since_pub = (current_year - root_year) if root_year else None
        if years_since_pub and years_since_pub > 0:
            citations_per_year = round(total_citation_count / years_since_pub, 2)
        else:
            citations_per_year = float(total_citation_count) if total_citation_count else 0.0

        # -- Analysis (b) & (c): Bridge papers and clusters --
        # Requires fetching references of top citers
        # Limit to top 20 citers by citation count to control API calls
        top_citers = sorted(
            citations,
            key=lambda p: p.get("citationCount", 0) or 0,
            reverse=True,
        )[:20]

        root_reference_ids: Set[str] = {
            ref.get("paperId", "") for ref in references if ref.get("paperId")
        }

        citer_reference_sets: Dict[str, Set[str]] = {}
        bridge_counter: Counter = Counter()

        for citer in top_citers:
            citer_id = citer.get("paperId")
            if not citer_id:
                continue

            await s2_limiter.wait()
            try:
                citer_refs = await _fetch_references_for_s2_paper(
                    client, citer_id, limit=200
                )
                citer_ref_set = set(citer_refs)
                citer_reference_sets[citer_id] = citer_ref_set

                # Bridge papers: references shared between root and this citer
                shared = root_reference_ids & citer_ref_set
                for ref_id in shared:
                    bridge_counter[ref_id] += 1
            except Exception as e:
                logger.debug(f"Rate limited or failed for citer {citer_id}: {e}")
                degradation_notes.append(
                    f"Skipped deep analysis for citer {citer_id}: {e}"
                )
                continue

        # Build bridge papers list — papers shared by multiple citers
        # Map paperId -> paper info from references
        ref_by_id: Dict[str, Dict[str, Any]] = {
            ref.get("paperId", ""): ref for ref in references if ref.get("paperId")
        }
        bridge_papers = []
        for ref_id, count in bridge_counter.most_common(15):
            if count < 2:
                break
            ref_info = ref_by_id.get(ref_id, {})
            bridge_papers.append({
                "title": ref_info.get("title", f"[S2:{ref_id[:12]}...]"),
                "year": ref_info.get("year"),
                "shared_by": count,
                "paperId": ref_id,
            })

        # -- Analysis (c): Citation Clusters --
        clusters = _find_clusters(top_citers, citer_reference_sets, threshold=0.3)

        # Build result
        result: Dict[str, Any] = {
            "paper": {
                "title": root_title,
                "year": root_year,
                "total_citations": total_citation_count,
                "total_references": total_reference_count,
            },
            "citation_velocity": {
                "citations_per_year": citations_per_year,
                "years_since_publication": years_since_pub,
            },
            "foundational_papers": foundational_papers,
            "temporal_impact": temporal_impact,
            "bridge_papers": bridge_papers,
            "citation_clusters": clusters,
        }

        if degradation_notes:
            result["notes"] = degradation_notes

        return [
            types.TextContent(type="text", text=json.dumps(result, indent=2))
        ]

    except ValueError as e:
        logger.error(f"Citation context error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    except Exception as e:
        logger.error(f"Unexpected citation context error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
