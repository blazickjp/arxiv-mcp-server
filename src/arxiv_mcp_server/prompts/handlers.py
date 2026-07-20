"""Handlers for prompt-related requests with paper analysis functionality."""

from typing import List, Dict
from mcp.types import Prompt, PromptMessage, TextContent, GetPromptResult
from .prompts import PROMPTS
from .deep_research_analysis_prompt import PAPER_ANALYSIS_PROMPT
from .summarize_paper_prompt import SUMMARIZE_PAPER_PROMPT
from .compare_papers_prompt import COMPARE_PAPERS_PROMPT
from .literature_review_prompt import LITERATURE_REVIEW_PROMPT

# Output structure for deep paper analysis
OUTPUT_STRUCTURE = """
Present your analysis with the following structure:
1. Executive Summary: 3-5 sentence overview of key contributions
2. Detailed Analysis: Following the specific focus requested
3. Visual Breakdown: Describe key figures/tables and their significance
4. Related Work Map: Position this paper within the research landscape
5. Implementation Notes: Practical considerations for applying these findings
"""


async def list_prompts() -> List[Prompt]:
    """Handle prompts/list request."""
    return list(PROMPTS.values())


async def get_prompt(
    name: str, arguments: Dict[str, str] | None = None, session_id: str | None = None
) -> GetPromptResult:
    """Handle prompts/get request for paper analysis.

    Args:
        name: The name of the prompt to get
        arguments: The arguments to use with the prompt
        session_id: Optional user session ID for context persistence

    Returns:
        GetPromptResult: The resulting prompt with messages

    Raises:
        ValueError: If prompt not found or arguments invalid
    """
    if name not in PROMPTS:
        raise ValueError(f"Prompt not found: {name}")

    prompt = PROMPTS[name]
    if arguments is None:
        raise ValueError(f"No arguments provided for prompt: {name}")

    # Validate required arguments
    for arg in prompt.arguments:
        if arg.required and (arg.name not in arguments or not arguments.get(arg.name)):
            raise ValueError(f"Missing required argument: {arg.name}")

    # Prompt generation is intentionally stateless. MCP does not currently pass
    # a reliable session lifecycle here, so retaining process-global paper IDs
    # would leak context between unrelated clients and grow without bound.
    paper_id = arguments.get("paper_id", "")

    if name == "deep-paper-analysis":
        content = (
            f"Analyze paper {paper_id}.\n\n"
            f"{OUTPUT_STRUCTURE}\n\n{PAPER_ANALYSIS_PROMPT}"
        )
    elif name == "summarize_paper":
        content = (
            f"Summarize paper {paper_id}.\n\n"
            "Use list_papers/download_paper/read_paper as needed before summarizing.\n\n"
            f"{SUMMARIZE_PAPER_PROMPT}"
        )
    elif name == "compare_papers":
        paper_ids = arguments.get("paper_ids", "")
        content = (
            f"Compare papers: {paper_ids}.\n\n"
            "Use list_papers/download_paper/read_paper to gather full text for each paper.\n\n"
            f"{COMPARE_PAPERS_PROMPT}"
        )
    else:
        topic = arguments.get("topic", "")
        paper_ids = arguments.get("paper_ids", "")
        optional_ids = f"\nFocus papers: {paper_ids}." if paper_ids else ""
        content = (
            f"Create a literature review on topic: {topic}.{optional_ids}\n\n"
            "Use search_papers to discover missing papers and read_paper to synthesize evidence.\n\n"
            f"{LITERATURE_REVIEW_PROMPT}"
        )

    return GetPromptResult(
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=content,
                ),
            )
        ]
    )
