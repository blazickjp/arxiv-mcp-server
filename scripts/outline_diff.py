#!/usr/bin/env python3
"""Compare paper outline parsing between main and current branch.

Downloads the 13 test papers from arXiv, parses them with both main's parser
and the current branch's parser, and reports differences per paper.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Test papers (arxiv_id, version, short_name, expected_main_sections)
TEST_PAPERS = [
    ("1706.03762", "v7", "Attention", 7),
    ("2101.03961", "v1", "Switch", 7),
    ("2106.09685", "v2", "LoRA", 9),
    ("2201.11903", "v6", "CoT", 11),
    ("2203.02155", "v1", "InstructGPT", None),
    ("2307.09288", "v2", "Llama2", None),
    ("2310.06825", "v2", "Mistral", None),
    ("2312.00752", "v2", "Mamba", None),
    ("2404.06422", "v1", "ACM", 24),
    ("2404.19756", "v5", "KAN", None),
    ("2410.17954", "v1", "ExpertFlow", 34),
    ("2501.10375", "v2", "DAOP", 21),
    ("2501.12948", "v1", "DeepSeek-R1", None),
]


async def download_paper(arxiv_id: str, version: str, storage_path: Path) -> Path | None:
    """Download a paper using the arxiv-mcp-server's download tool."""
    paper_file = storage_path / f"{arxiv_id}{version}.md"
    if paper_file.exists():
        print(f"  Already cached: {paper_file.name}")
        return paper_file

    print(f"  Downloading {arxiv_id}{version}...")
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from arxiv_mcp_server.tools.download import handle_download
        from arxiv_mcp_server.config import Settings

        settings = Settings()
        original_storage = settings.STORAGE_PATH
        settings.STORAGE_PATH = str(storage_path)

        result = await handle_download({
            "paper_id": f"{arxiv_id}{version}",
            "include_markdown": True,
        })

        settings.STORAGE_PATH = original_storage

        if paper_file.exists():
            print(f"  Downloaded: {paper_file.name} ({paper_file.stat().st_size} bytes)")
            return paper_file
        else:
            print(f"  Failed to download {arxiv_id}{version}")
            return None
    except Exception as e:
        print(f"  Error downloading {arxiv_id}{version}: {e}")
        import traceback
        traceback.print_exc()
        return None


def parse_with_main(content: str) -> list[dict[str, Any]]:
    """Parse sections using main's parser."""
    # Extract main's paper_outline.py from git
    try:
        result = subprocess.run(
            ["git", "show", "main:src/arxiv_mcp_server/tools/paper_outline.py"],
            capture_output=True,
            text=True,
            check=True,
        )
        main_code = result.stdout

        # Create a temporary module with main's code
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(main_code)
            temp_path = f.name

        # Import and use it
        import importlib.util
        spec = importlib.util.spec_from_file_location("paper_outline_main", temp_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sections = module.parse_markdown_sections(content)
        Path(temp_path).unlink()

        return [{"id": s.section_id, "level": s.level, "title": s.title} for s in sections]
    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not get main's parser: {e}")
        return []
    except Exception as e:
        print(f"Warning: Error parsing with main: {e}")
        return []


def parse_with_branch(content: str) -> list[dict[str, Any]]:
    """Parse sections using the current branch's parser."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arxiv_mcp_server.tools.paper_outline import parse_markdown_sections

    sections = parse_markdown_sections(content)
    return [{"id": s.section_id, "level": s.level, "title": s.title} for s in sections]


def compare_outlines(
    main_sections: list[dict], branch_sections: list[dict]
) -> dict[str, Any]:
    """Compare two outlines and return differences."""
    main_titles = set(s["title"] for s in main_sections)
    branch_titles = set(s["title"] for s in branch_sections)

    return {
        "main_count": len(main_sections),
        "branch_count": len(branch_sections),
        "fakes_added": sorted(branch_titles - main_titles),
        "real_lost": sorted(main_titles - branch_titles),
        "main_titles": [s["title"] for s in main_sections],
        "branch_titles": [s["title"] for s in branch_sections],
    }


async def main():
    """Download papers and compare outlines."""
    workspace = Path(__file__).parent.parent
    storage_path = workspace / "test_paper_cache"
    storage_path.mkdir(exist_ok=True)

    print("=" * 80)
    print("DOWNLOADING PAPERS")
    print("=" * 80)

    papers_data = []
    for arxiv_id, version, name, expected_main in TEST_PAPERS:
        print(f"\n{name} ({arxiv_id}{version}):")
        paper_file = await download_paper(arxiv_id, version, storage_path)
        if paper_file:
            content = paper_file.read_text(encoding="utf-8")
            papers_data.append({
                "arxiv_id": arxiv_id,
                "version": version,
                "name": name,
                "content": content,
                "expected_main": expected_main,
            })

    print("\n" + "=" * 80)
    print("COMPARING OUTLINES (MAIN VS BRANCH)")
    print("=" * 80)

    results = []
    for paper in papers_data:
        print(f"\n{paper['name']} ({paper['arxiv_id']}{paper['version']}):")

        # Parse with both versions
        main_sections = parse_with_main(paper['content'])
        branch_sections = parse_with_branch(paper['content'])

        if not main_sections:
            print("  WARNING: Could not parse with main (using branch only)")
            print(f"  Branch: {len(branch_sections)} sections")
            results.append({
                "name": paper["name"],
                "arxiv_id": paper["arxiv_id"],
                "version": paper["version"],
                "main_count": "?",
                "branch_count": len(branch_sections),
                "fakes_added": "?",
                "real_lost": "?",
                "branch_titles": [s["title"] for s in branch_sections],
            })
            continue

        comparison = compare_outlines(main_sections, branch_sections)
        print(f"  Main: {comparison['main_count']} sections")
        print(f"  Branch: {comparison['branch_count']} sections")

        if comparison['fakes_added']:
            print(f"  Fakes added: {comparison['fakes_added']}")
        if comparison['real_lost']:
            print(f"  Real lost: {comparison['real_lost']}")

        results.append({
            "name": paper["name"],
            "arxiv_id": paper["arxiv_id"],
            "version": paper["version"],
            **comparison,
        })

    # Print summary table
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Paper':<15} {'ArXiv ID':<16} {'Main':<6} {'Branch':<6} {'Fakes':<8} {'Lost':<6}")
    print("-" * 80)
    for r in results:
        main_str = str(r["main_count"]) if r["main_count"] != "?" else "?"
        fakes_str = str(len(r["fakes_added"])) if r["fakes_added"] != "?" else "?"
        lost_str = str(len(r["real_lost"])) if r["real_lost"] != "?" else "?"
        print(
            f"{r['name']:<15} {r['arxiv_id']:<16} {main_str:<6} "
            f"{r['branch_count']:<6} {fakes_str:<8} {lost_str:<6}"
        )

    # Save detailed results
    output_file = workspace / "outline_diff_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results saved to: {output_file}")

    # Check key requirements
    print("\n" + "=" * 80)
    print("REQUIREMENT CHECKS")
    print("=" * 80)

    for r in results:
        if r["name"] == "KAN":
            titles = set(r.get("branch_titles", []))
            print(f"\nKAN sections check:")
            print(f"  Has 'Kolmogorov-Arnold Networks': {'Kolmogorov-Arnold Networks' in titles}")
            print(f"  Has 'KAN architecture': {'KAN architecture' in titles}")
            print(f"  Has 'KANs are accurate': {'KANs are accurate' in titles}")
            print(f"  Has 'KANs are interpretable': {'KANs are interpretable' in titles}")

        if r["name"] in ["DAOP", "ExpertFlow", "ACM"]:
            expected = r.get("expected_main")
            actual = r["branch_count"]
            status = "✓" if expected == actual else "✗"
            print(f"\n{r['name']}: Expected {expected}, got {actual} {status}")


if __name__ == "__main__":
    asyncio.run(main())
