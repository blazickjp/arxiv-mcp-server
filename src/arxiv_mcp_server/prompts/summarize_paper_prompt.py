"""Prompt template for concise paper summarization."""

SUMMARIZE_PAPER_PROMPT = """
Produce a concise, technically accurate summary of the target paper.

Required structure:
1. Problem and motivation (2-3 sentences)
2. Core method or approach (3-5 bullet points)
3. Main results (metrics, datasets, or key evidence)
4. Strengths and limitations
5. Practical takeaway for researchers

Keep the summary factual, avoid speculation, and cite evidence from the paper text.
"""
