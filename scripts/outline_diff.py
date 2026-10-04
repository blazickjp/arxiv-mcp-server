#!/usr/bin/env python3
"""Compare paper outline parsing between main and current branch.

Loads main's parser via `git show` and branch's parser from the working tree
as separate modules to ensure they can't be the same. Classifies headings as
REAL or FAKE based on table/figure row patterns, not just "not in main".
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Add src to path so imports work
workspace = Path(__file__).parent.parent
sys.path.insert(0, str(workspace / "src"))

# Test papers available in cache (13 total)
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
    ("2201.11903", "ChainOfThought", None),
    ("2104.09864", "DeepSeek-R1-Zero", None),
    ("2501.12948", "DeepSeek-R1", None),
]

# Known fake headings from rounds 2 and 3 (table/figure rows, model names)
KNOWN_FAKES = {
    # Round 2 fakes (table rows, model names)
    "GPT-4o", "GPT-4o-mini", "Qwen2.5", "DeepSeek-V3", "Claude-3.5-Sonnet",
    "Gemini-2.0-Flash", "Llama-3.3-70B", "Gemini-1.5-Pro", "Llama-3.1-405B",
    "Mistral-Large-2", "GPT-4-Turbo", "Qwen2.5-72B", "Falcon-180B",
    "Mixtral 8x7B", "Grok 2", "Claude 3 Opus",  # DAOP table labels
    # Round 3 fakes (table numbers, dataset rows)
    "12", "16", "64",  # Switch Transformer table numbers
    "60", "80", "90",  # Chain of Thought dataset rows
}


def is_fake_heading(title: str) -> bool:
    """Check if a heading looks like a table/figure row or data line.
    
    A heading is fake if it:
    - Contains % or = (data values)
    - Contains : followed by digits/special chars (key-value pairs)
    - Is in the known fakes list (model names, table numbers)
    - Starts with Table/Figure/Algorithm/Equation prefix followed by number
    - Is very short (<3 chars) and looks like a table cell
    """
    title = title.strip()
    
    # Known fakes from previous rounds
    if title in KNOWN_FAKES:
        return True
    
    # Data line patterns
    if re.search(r"[%=]", title):
        return True
    if re.search(r":\s*[\d.-]", title):
        return True
    
    # Table/figure prefixes
    if re.match(r"^(?:Table|Figure|Fig\.|Algorithm|Eq\.|Equation)\s+\d", title, re.IGNORECASE):
        return True
    
    # Very short tokens (likely table cells or model abbreviations)
    if len(title) < 3:
        return True
    
    # Pure numbers (table row numbers)
    if re.match(r"^\d+$", title):
        return True
    
    return False


def parse_with_main(content: str) -> tuple[list[dict[str, Any]], str, int]:
    """Parse sections using main's parser loaded from git.
    
    Returns:
        (sections, sha, line_count)
    """
    # Get main's file content via git show
    result = subprocess.run(
        ["git", "show", "main:src/arxiv_mcp_server/tools/paper_outline.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    main_content = result.stdout
    
    # Get main's SHA and line count
    sha_result = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    main_sha = sha_result.stdout.strip()[:7]
    line_count = len(main_content.splitlines())
    
    # Write to temp file in a package structure
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        pkg_dir = tmppath / "arxiv_mcp_server_main" / "tools"
        pkg_dir.mkdir(parents=True)
        
        # Create __init__.py files
        (tmppath / "arxiv_mcp_server_main" / "__init__.py").write_text("")
        (pkg_dir / "__init__.py").write_text("")
        
        # Write the paper_outline.py with relative imports converted
        # Replace relative imports with stubs
        modified_content = main_content
        # Stub out the config import
        stub = """
class Settings:
    def __init__(self):
        self.MAX_RESULTS = 100
"""
        modified_content = f"{stub}\n{modified_content}"
        modified_content = modified_content.replace("from ..config import Settings", "# from ..config import Settings")
        
        (pkg_dir / "paper_outline.py").write_text(modified_content)
        
        # Add to path and import
        sys.path.insert(0, str(tmppath))
        try:
            from arxiv_mcp_server_main.tools.paper_outline import parse_markdown_sections
            sections = parse_markdown_sections(content)
            return (
                [{"id": s.section_id, "level": s.level, "title": s.title} for s in sections],
                main_sha,
                line_count,
            )
        finally:
            sys.path.remove(str(tmppath))
            # Clean up imports
            if "arxiv_mcp_server_main.tools.paper_outline" in sys.modules:
                del sys.modules["arxiv_mcp_server_main.tools.paper_outline"]
            if "arxiv_mcp_server_main.tools" in sys.modules:
                del sys.modules["arxiv_mcp_server_main.tools"]
            if "arxiv_mcp_server_main" in sys.modules:
                del sys.modules["arxiv_mcp_server_main"]


def parse_with_branch(content: str) -> tuple[list[dict[str, Any]], str, int]:
    """Parse sections using branch's parser from working tree.
    
    Returns:
        (sections, sha, line_count)
    """
    branch_file = workspace / "src" / "arxiv_mcp_server" / "tools" / "paper_outline.py"
    
    # Get current commit SHA and line count
    sha_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    branch_sha = sha_result.stdout.strip()[:7]
    line_count = len(branch_file.read_text().splitlines())
    
    # Import directly from the package
    from arxiv_mcp_server.tools.paper_outline import parse_markdown_sections
    
    sections = parse_markdown_sections(content)
    
    return (
        [{"id": s.section_id, "level": s.level, "title": s.title} for s in sections],
        branch_sha,
        line_count,
    )


def main():
    """Compare outlines for all cached papers."""
    cache_dir = workspace / "test_paper_cache" / "cache_papers"

    if not cache_dir.exists():
        print(f"ERROR: Cache directory not found: {cache_dir}")
        print("Please extract cache_papers_13.tgz to test_paper_cache/")
        return 1

    print("=" * 80)
    print("COMPARING OUTLINES (MAIN VS BRANCH)")
    print("=" * 80)

    # Get parser versions first
    try:
        main_sha_result = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
        main_sha = main_sha_result.stdout.strip()[:7]
        
        branch_sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
        branch_sha = branch_sha_result.stdout.strip()[:7]
        
        # Get line counts
        main_result = subprocess.run(
            ["git", "show", "main:src/arxiv_mcp_server/tools/paper_outline.py"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
        main_lines = len(main_result.stdout.splitlines())
        
        branch_file = workspace / "src" / "arxiv_mcp_server" / "tools" / "paper_outline.py"
        branch_lines = len(branch_file.read_text().splitlines())
        
        print(f"\nParser versions:")
        print(f"  Main:   {main_sha} ({main_lines} lines)")
        print(f"  Branch: {branch_sha} ({branch_lines} lines)")
        print()
    except Exception as e:
        print(f"Warning: Could not get parser versions: {e}")
        main_sha = "unknown"
        branch_sha = "unknown"

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
            main_sections, _, _ = parse_with_main(content)
        except Exception as e:
            print(f"  ERROR parsing with main: {e}")
            main_sections = []

        # Parse with branch
        try:
            branch_sections, _, _ = parse_with_branch(content)
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
                "real_gained": [],
                "real_lost": [],
                "main_titles": [],
                "branch_titles": [s["title"] for s in branch_sections],
            })
            continue

        main_titles = {s["title"] for s in main_sections}
        branch_titles = {s["title"] for s in branch_sections}

        # Classify new headings as REAL or FAKE
        new_in_branch = sorted(branch_titles - main_titles)
        fakes_added = [t for t in new_in_branch if is_fake_heading(t)]
        real_gained = [t for t in new_in_branch if not is_fake_heading(t)]
        
        # Headings lost from main (should be rare - usually means regression)
        real_lost = sorted(main_titles - branch_titles)

        print(f"  Main: {len(main_sections)} sections")
        print(f"  Branch: {len(branch_sections)} sections")

        if fakes_added:
            print(f"  Fakes added: {fakes_added}")
        if real_gained:
            print(f"  Real gained: {real_gained}")
        if real_lost:
            print(f"  Real lost: {real_lost}")

        results.append({
            "name": name,
            "arxiv_id": arxiv_id,
            "main_count": len(main_sections),
            "branch_count": len(branch_sections),
            "fakes_added": fakes_added,
            "real_gained": real_gained,
            "real_lost": real_lost,
            "main_titles": [s["title"] for s in main_sections],
            "branch_titles": [s["title"] for s in branch_sections],
        })

    # Print summary table
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Paper':<15} {'ArXiv ID':<16} {'Main':<6} {'Branch':<6} {'Fakes':<8} {'Real+':<8} {'Lost':<6}")
    print("-" * 80)
    for r in results:
        main_str = str(r["main_count"]) if r["main_count"] != "?" else "?"
        fakes = len(r["fakes_added"])
        gained = len(r["real_gained"])
        lost = len(r["real_lost"])
        print(
            f"{r['name']:<15} {r['arxiv_id']:<16} {main_str:<6} "
            f"{r['branch_count']:<6} {fakes:<8} {gained:<8} {lost:<6}"
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
            expected = {"DAOP": 21, "ExpertFlow": 34, "ACM": 24}[r["name"]]
            status = "✓" if branch_c == expected else "✗"
            print(f"\n{r['name']}: Main={main_c}, Branch={branch_c}, Expected={expected} {status}")
            if r["fakes_added"]:
                print(f"  Fakes: {r['fakes_added']}")
            if branch_c != expected:
                print(f"  ERROR: {r['name']} must be exactly {expected} sections!")

    # Check for recovered headings from round 2
    print("\n" + "=" * 80)
    print("RECOVERED HEADINGS (vs round 2)")
    print("=" * 80)
    print("Expected recoveries from round 2:")
    print("  - Attention: '3 Model Architecture'")
    print("  - Switch: '2 Switch Transformer'")
    print("  - Mistral: '2 Architectural details'")
    print("  - DeepSeek-R1-Zero: '3 Experimental setup'")
    
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
        elif r["name"] == "DeepSeek-R1-Zero":
            has_exp_setup = any("Experimental setup" in t for t in r["branch_titles"])
            print(f"  DeepSeek-R1-Zero has 'Experimental setup': {has_exp_setup}")

    # Check for rejections (table numbers that should NOT appear)
    print("\n" + "=" * 80)
    print("REJECTION CHECKS (should NOT appear)")
    print("=" * 80)
    print("These should be rejected (top-level cap or table rows):")
    print("  - Switch: table numbers 12, 16, 64 (after section 2)")
    print("  - Chain of Thought: dataset rows 60, 80, 90")
    
    for r in results:
        if r["name"] == "Switch":
            bad_nums = [t for t in ["12", "16", "64"] if t in r["branch_titles"]]
            if bad_nums:
                print(f"  ERROR: Switch has table numbers {bad_nums} ✗")
            else:
                print(f"  Switch correctly rejects 12/16/64 ✓")
        elif r["name"] == "ChainOfThought":
            bad_nums = [t for t in ["60", "80", "90"] if t in r["branch_titles"]]
            if bad_nums:
                print(f"  ERROR: ChainOfThought has dataset rows {bad_nums} ✗")
            else:
                print(f"  ChainOfThought correctly rejects 60/80/90 ✓")

    return 0


if __name__ == "__main__":
    sys.exit(main())
