#!/usr/bin/env python3
"""Simple outline comparison by checking out each version temporarily."""

import subprocess
import sys
from pathlib import Path

workspace = Path(__file__).parent.parent
sys.path.insert(0, str(workspace / "src"))

TEST_PAPERS = [
    ("1706.03762", "Attention"),
    ("2101.03961", "Switch"),
    ("2106.09685", "LoRA"),
    ("2307.09288", "Llama2"),
    ("2310.06825", "Mistral"),
    ("2312.00752", "Mamba"),
    ("2404.06422", "ACM"),
    ("2404.19756", "KAN"),
    ("2410.17954", "ExpertFlow"),
    ("2501.10375", "DAOP"),
    ("2201.11903", "ChainOfThought"),
    ("2104.09864", "DeepSeek-R1-Zero"),
    ("2501.12948", "DeepSeek-R1"),
]


def parse_all(cache_dir):
    """Parse all papers with current checkout."""
    for mod in list(sys.modules.keys()):
        if "arxiv_mcp_server" in mod:
            del sys.modules[mod]
    
    from arxiv_mcp_server.tools.paper_outline import parse_markdown_sections
    
    results = {}
    for arxiv_id, name in TEST_PAPERS:
        paper_file = cache_dir / f"{arxiv_id}.md"
        if paper_file.exists():
            content = paper_file.read_text(encoding="utf-8")
            sections = parse_markdown_sections(content)
            results[name] = {"count": len(sections), "titles": [s.title for s in sections]}
        else:
            results[name] = {"count": 0, "titles": []}
    return results


def main():
    cache_dir = workspace / "test_paper_cache" / "cache_papers"
    
    # Get branch name
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=workspace, capture_output=True, text=True, check=True
    )
    branch = result.stdout.strip()
    
    # Parse with main
    print("Parsing with main...")
    subprocess.run(["git", "checkout", "main"], cwd=workspace, check=True, capture_output=True)
    main_results = parse_all(cache_dir)
    
    # Parse with branch
    print(f"Parsing with {branch}...")
    subprocess.run(["git", "checkout", branch], cwd=workspace, check=True, capture_output=True)
    branch_results = parse_all(cache_dir)
    
    # Show results
    print("\n" + "=" * 70)
    print(f"{'Paper':<16} {'Main':<6} {'Branch':<6} {'Diff':<6}")
    print("-" * 70)
    
    for arxiv_id, name in TEST_PAPERS:
        m = main_results[name]["count"]
        b = branch_results[name]["count"]
        d = b - m
        print(f"{name:<16} {m:<6} {b:<6} {'+' + str(d) if d >= 0 else str(d):<6}")
    
    # Check requirements
    print("\nRequirements:")
    print(f"  DAOP = 21: {branch_results['DAOP']['count']} {'✓' if branch_results['DAOP']['count'] == 21 else '✗'}")
    print(f"  ACM = 24: {branch_results['ACM']['count']} {'✓' if branch_results['ACM']['count'] == 24 else '✗'}")
    print(f"  ExpertFlow = 34: {branch_results['ExpertFlow']['count']} {'✓' if branch_results['ExpertFlow']['count'] == 34 else '✗'}")
    
    kan_titles = set(branch_results["KAN"]["titles"])
    print(f"  KAN has 2.2: {'KAN architecture' in kan_titles} {'✓' if 'KAN architecture' in kan_titles else '✗'}")
    
    switch_titles = set(branch_results["Switch"]["titles"])
    bad = [t for t in ["12", "16", "64"] if t in switch_titles]
    print(f"  Switch rejects 12/16/64: {not bad} {'✓' if not bad else '✗ has ' + str(bad)}")


if __name__ == "__main__":
    main()
