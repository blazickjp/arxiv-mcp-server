#!/usr/bin/env python3
"""Compare paper outline parsing between main and current branch.

Uses the cached papers from cache_papers_13.tgz, parses them with both
main's parser and the current branch's parser, and reports differences.
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

# Test papers available in cache (10 total)
TEST_PAPERS = [
    ("1706.03762", "Attention", 7),
    ("2101.03961", "Switch", 7),
    ("2106.09685", "LoRA", 9),
    ("2307.09288", "Llama2", None),
    ("2310.06825", "Mistral", None),
    ("2312.00752", "Mamba", None),
    ("2404.06422", "ACM", 24),
    ("2404.19756", "KAN", None),
    ("2410.17954", "ExpertFlow", 34),
    ("2501.10375", "DAOP", 21),
]


def parse_with_git_version(content: str, git_ref: str) -> list[dict[str, Any]]:
    """Parse sections by checking out a git version temporarily."""
    workspace = Path(__file__).parent.parent
    
    # Save current file
    current_file = workspace / "src" / "arxiv_mcp_server" / "tools" / "paper_outline.py"
    backup_content = current_file.read_text()
    
    try:
        # Checkout the file from git ref
        subprocess.run(
            ["git", "checkout", git_ref, "--", "src/arxiv_mcp_server/tools/paper_outline.py"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        
        # Import and parse
        sys.path.insert(0, str(workspace / "src"))
        
        # Force reload
        if "arxiv_mcp_server.tools.paper_outline" in sys.modules:
            del sys.modules["arxiv_mcp_server.tools.paper_outline"]
        if "arxiv_mcp_server.tools" in sys.modules:
            del sys.modules["arxiv_mcp_server.tools"]
        
        from arxiv_mcp_server.tools.paper_outline import parse_markdown_sections
        sections = parse_markdown_sections(content)
        
        return [{"id": s.section_id, "level": s.level, "title": s.title} for s in sections]
        
    finally:
        # Restore current file
        current_file.write_text(backup_content)
        
        # Reload current version
        if "arxiv_mcp_server.tools.paper_outline" in sys.modules:
            del sys.modules["arxiv_mcp_server.tools.paper_outline"]
        if "arxiv_mcp_server.tools" in sys.modules:
            del sys.modules["arxiv_mcp_server.tools"]


def parse_with_branch(content: str) -> list[dict[str, Any]]:
    """Parse sections using the current branch's parser."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from arxiv_mcp_server.tools.paper_outline import parse_markdown_sections

    sections = parse_markdown_sections(content)
    return [{"id": s.section_id, "level": s.level, "title": s.title} for s in sections]


def main():
    """Compare outlines for all cached papers."""
    workspace = Path(__file__).parent.parent
    cache_dir = workspace / "test_paper_cache" / "cache_papers"

    if not cache_dir.exists():
        print(f"ERROR: Cache directory not found: {cache_dir}")
        print("Please extract cache_papers_13.tgz to test_paper_cache/")
        return 1

    print("=" * 80)
    print("COMPARING OUTLINES (MAIN VS BRANCH)")
    print("=" * 80)

    results = []
    for arxiv_id, name, expected_main in TEST_PAPERS:
        paper_file = cache_dir / f"{arxiv_id}.md"
        if not paper_file.exists():
            print(f"\n{name} ({arxiv_id}): FILE NOT FOUND")
            continue

        print(f"\n{name} ({arxiv_id}):")
        content = paper_file.read_text(encoding="utf-8")

        # Parse with main
        try:
            main_sections = parse_with_git_version(content, "main")
        except Exception as e:
            print(f"  ERROR parsing with main: {e}")
            main_sections = []

        # Parse with branch
        try:
            branch_sections = parse_with_branch(content)
        except Exception as e:
            print(f"  ERROR parsing with branch: {e}")
            continue

        if not main_sections:
            print(f"  WARNING: Could not parse with main (showing branch only)")
            print(f"  Branch: {len(branch_sections)} sections")
            results.append({
                "name": name,
                "arxiv_id": arxiv_id,
                "main_count": "?",
                "branch_count": len(branch_sections),
                "fakes_added": [],
                "real_lost": [],
                "main_titles": [],
                "branch_titles": [s["title"] for s in branch_sections],
            })
            continue

        main_titles = {s["title"] for s in main_sections}
        branch_titles = {s["title"] for s in branch_sections}

        fakes_added = sorted(branch_titles - main_titles)
        real_lost = sorted(main_titles - branch_titles)

        print(f"  Main: {len(main_sections)} sections")
        print(f"  Branch: {len(branch_sections)} sections")

        if fakes_added:
            print(f"  Fakes added: {fakes_added}")
        if real_lost:
            print(f"  Real lost: {real_lost}")

        results.append({
            "name": name,
            "arxiv_id": arxiv_id,
            "main_count": len(main_sections),
            "branch_count": len(branch_sections),
            "fakes_added": fakes_added,
            "real_lost": real_lost,
            "main_titles": [s["title"] for s in main_sections],
            "branch_titles": [s["title"] for s in branch_sections],
        })

    # Print summary table
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Paper':<15} {'ArXiv ID':<16} {'Main':<6} {'Branch':<6} {'Fakes':<8} {'Lost':<6}")
    print("-" * 80)
    for r in results:
        main_str = str(r["main_count"]) if r["main_count"] != "?" else "?"
        fakes = len(r["fakes_added"])
        lost = len(r["real_lost"])
        print(
            f"{r['name']:<15} {r['arxiv_id']:<16} {main_str:<6} "
            f"{r['branch_count']:<6} {fakes:<8} {lost:<6}"
        )

    # Check key requirements
    print("\n" + "=" * 80)
    print("REQUIREMENT CHECKS")
    print("=" * 80)

    for r in results:
        if r["name"] == "KAN":
            titles = set(r["branch_titles"])
            has_2 = any("Kolmogorov-Arnold" in t for t in titles)
            has_22 = "KAN architecture" in titles
            has_3 = "KANs are accurate" in titles
            has_4 = "KANs are interpretable" in titles
            
            print(f"\nKAN sections check:")
            print(f"  Has section 2 (Kolmogorov-Arnold...): {has_2}")
            print(f"  Has section 2.2 (KAN architecture): {has_22}")
            print(f"  Has section 3 (KANs are accurate): {has_3}")
            print(f"  Has section 4 (KANs are interpretable): {has_4}")
            
            # Check Introduction ending
            try:
                intro_idx = r["branch_titles"].index("Introduction")
                next_title = r["branch_titles"][intro_idx+1] if intro_idx+1 < len(r["branch_titles"]) else None
                print(f"  Introduction followed by: {next_title}")
                print(f"  (Should be Kolmogorov-Arnold Networks, not part of Introduction)")
            except ValueError:
                print(f"  Introduction not found")

        if r["name"] in ["DAOP", "ExpertFlow", "ACM"]:
            main_c = r["main_count"]
            branch_c = r["branch_count"]
            status = "✓" if main_c == branch_c else "✗"
            print(f"\n{r['name']}: Main={main_c}, Branch={branch_c} {status}")
            if r["fakes_added"]:
                print(f"  Fakes: {r['fakes_added']}")

    # Check for recovered headings from round 2
    print("\n" + "=" * 80)
    print("RECOVERED HEADINGS (vs round 2)")
    print("=" * 80)
    print("Expected recoveries from round 2:")
    print("  - Attention: '3 Model Architecture'")
    print("  - Switch: '2 Switch Transformer'")
    print("  - Mistral: '2 Architectural details'")
    
    for r in results:
        if r["name"] == "Attention":
            has_model_arch = any("Model Architecture" in t for t in r["branch_titles"])
            print(f"  Attention has 'Model Architecture': {has_model_arch}")
        elif r["name"] == "Switch":
            has_switch_trans = any("Switch Transformer" in t for t in r["branch_titles"])
            print(f"  Switch has 'Switch Transformer': {has_switch_trans}")
        elif r["name"] == "Mistral":
            has_arch_details = any("Architectural details" in t for t in r["branch_titles"])
            print(f"  Mistral has 'Architectural details': {has_arch_details}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
