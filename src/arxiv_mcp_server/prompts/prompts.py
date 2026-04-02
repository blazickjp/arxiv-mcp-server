"""Prompt definitions for arXiv MCP server with research journey support."""

from mcp.types import (
    Prompt,
    PromptArgument,
)

# Define all prompts
PROMPTS = {
    "research-discovery": Prompt(
        name="research-discovery",
        description="Begin research exploration on a specific topic",
        arguments=[
            PromptArgument(
                name="topic", description="Research topic or question", required=True
            ),
            PromptArgument(
                name="expertise_level",
                description="User's familiarity (beginner/intermediate/expert)",
                required=False,
            ),
            PromptArgument(
                name="time_period",
                description="Time period for search (e.g., '2023-present')",
                required=False,
            ),
            PromptArgument(
                name="domain",
                description="Academic domain (e.g., computer_science/physics/biology)",
                required=False,
            ),
        ],
    ),
    "deep-paper-analysis": Prompt(
        name="deep-paper-analysis",
        description="Analyze a specific paper in detail",
        arguments=[
            PromptArgument(
                name="paper_id", description="arXiv paper ID", required=True
            ),
        ],
    ),
    "summarize_paper": Prompt(
        name="summarize_paper",
        description="Summarize a paper with key methods, results, and limits",
        arguments=[
            PromptArgument(
                name="paper_id", description="arXiv paper ID", required=True
            ),
        ],
    ),
    "compare_papers": Prompt(
        name="compare_papers",
        description="Compare two or more papers on methods and results",
        arguments=[
            PromptArgument(
                name="paper_ids",
                description="Comma-separated list of arXiv paper IDs",
                required=True,
            ),
        ],
    ),
    "literature_review": Prompt(
        name="literature_review",
        description="Synthesize a literature review for a topic and paper set",
        arguments=[
            PromptArgument(
                name="topic", description="Research topic or question", required=True
            ),
            PromptArgument(
                name="paper_ids",
                description="Comma-separated list of arXiv paper IDs",
                required=False,
            ),
        ],
    ),
    "literature-synthesis": Prompt(
        name="literature-synthesis",
        description="Synthesize findings across multiple papers",
        arguments=[
            PromptArgument(
                name="paper_ids",
                description="Comma-separated list of arXiv paper IDs",
                required=True,
            ),
            PromptArgument(
                name="synthesis_type",
                description="Synthesis type (themes/methods/timeline/gaps/comprehensive)",
                required=False,
            ),
            PromptArgument(
                name="domain",
                description="Academic domain (e.g., computer_science/physics/biology)",
                required=False,
            ),
        ],
    ),
    "research-question": Prompt(
        name="research-question",
        description="Formulate research questions based on literature",
        arguments=[
            PromptArgument(
                name="paper_ids",
                description="Comma-separated list of arXiv paper IDs",
                required=True,
            ),
            PromptArgument(
                name="topic", description="Research topic or question", required=True
            ),
            PromptArgument(
                name="domain",
                description="Academic domain (e.g., computer_science/physics/biology)",
                required=False,
            ),
        ],
    ),
}
