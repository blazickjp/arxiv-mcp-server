"""Resource management and storage for arXiv papers."""

from pathlib import Path
from typing import List
import arxiv
import pymupdf4llm
import aiofiles
import logging
from pydantic import AnyUrl
import mcp.types as types
from ..config import Settings

logger = logging.getLogger("research-mcp-server")


class PaperManager:
    """Manages the storage, retrieval, and resource handling of arXiv papers."""

    def __init__(self):
        """Initialize the paper management system."""
        settings = Settings()
        self.storage_path = Path(settings.STORAGE_PATH)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.client = arxiv.Client()

    def _get_paper_path(self, paper_id: str) -> Path:
        """Get the absolute file path for a paper."""
        return self.storage_path / f"{paper_id}.md"

    async def store_paper(self, paper_id: str, pdf_url: str) -> bool:
        """Download and store a paper from arXiv."""
        paper_md_path = self._get_paper_path(paper_id)
        paper_pdf_path = paper_md_path.with_suffix(".pdf")

        if paper_md_path.exists():
            return True

        try:
            paper = next(self.client.results(arxiv.Search(id_list=[paper_id])))
            paper.download_pdf(dirpath=self.storage_path, filename=paper_pdf_path)
            markdown = pymupdf4llm.to_markdown(paper_pdf_path, show_progress=False)

            async with aiofiles.open(paper_md_path, "w", encoding="utf-8") as f:
                await f.write(markdown)

            return True

        except StopIteration:
            raise ValueError(f"Paper with ID {paper_id} not found on arXiv.")
        except arxiv.ArxivError as e:
            raise ValueError(
                f"Error: Failed to download paper {paper_id} from arXiv. Details: {str(e)}"
            )
        except Exception as e:
            raise ValueError(
                f"Error: An unexpected error occurred while storing paper {paper_id}. Details: {str(e)}"
            )

    async def has_paper(self, paper_id: str) -> bool:
        """Check if a paper is available in storage."""
        return self._get_paper_path(paper_id).exists()

    async def list_papers(self) -> list[str]:
        """List all stored paper IDs."""
        logger.info(f"Listing papers in {self.storage_path}")
        paper_ids = [p.stem for p in self.storage_path.glob("*.md")]
        logger.info(f"Found {len(paper_ids)} papers")
        return paper_ids

    async def list_resources(self) -> List[types.Resource]:
        """List all papers as MCP resources with metadata."""
        paper_ids = await self.list_papers()
        resources = []

        for paper_id in paper_ids:
            search = arxiv.Search(id_list=[paper_id])
            papers = list(self.client.results(search))

            if papers:
                paper = papers[0]
                paper_path = self._get_paper_path(paper_id)
                resources.append(
                    types.Resource(
                        uri=AnyUrl(f"file://{str(paper_path)}"),
                        name=paper.title,
                        description=paper.summary,
                        mimeType="text/markdown",
                    )
                )

        logger.info(f"Found {len(resources)} resources")
        return resources

    async def get_paper_content(self, paper_id: str) -> str:
        """Get the markdown content of a stored paper."""
        paper_path = self._get_paper_path(paper_id)
        if not paper_path.exists():
            raise ValueError(f"Paper {paper_id} not found in storage")

        async with aiofiles.open(paper_path, "r", encoding="utf-8") as f:
            return await f.read()
