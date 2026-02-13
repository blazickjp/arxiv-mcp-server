"""Prompt template for side-by-side paper comparisons."""

COMPARE_PAPERS_PROMPT = """
Compare the provided papers with a focus on technical differences and tradeoffs.

Required structure:
1. Shared problem definition and scope
2. Method comparison table (assumptions, architecture, training setup)
3. Results comparison (benchmarks, metrics, and caveats)
4. Strengths, weaknesses, and failure modes
5. Recommendation: when to choose each approach

Use concrete evidence from each paper; call out missing details explicitly.
"""
