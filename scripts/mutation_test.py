#!/usr/bin/env python3
"""Mutation testing for paper_outline.py guards and rules."""

import subprocess
import sys
from pathlib import Path


def run_tests():
    """Run outline tests and return pass/fail counts."""
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/tools/test_paper_outline.py", "-q"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    output = result.stdout + result.stderr

    # Parse output like "46 passed in 0.78s" or "1 failed, 45 passed in 0.82s"
    if "failed" in output:
        return "FAILED"
    elif "passed" in output:
        return "PASSED"
    else:
        return f"UNKNOWN: {output[-200:]}"


def test_mutation(name: str, original: str, mutated: str):
    """Test a mutation by replacing code and running tests."""
    file_path = (
        Path(__file__).parent.parent
        / "src"
        / "arxiv_mcp_server"
        / "tools"
        / "paper_outline.py"
    )

    # Read original
    original_content = file_path.read_text()

    if original not in original_content:
        print(f"  ✗ Pattern not found in file!")
        return

    # Apply mutation
    mutated_content = original_content.replace(original, mutated)
    file_path.write_text(mutated_content)

    # Run tests
    result = run_tests()

    # Restore
    file_path.write_text(original_content)

    # Report
    if result == "FAILED":
        print(f"  ✓ Tests FAILED (guard is effective)")
    elif result == "PASSED":
        print(f"  ✗ Tests PASSED (guard NOT effective!)")
    else:
        print(f"  ? {result}")


def main():
    """Run all mutation tests."""
    print("=" * 80)
    print("MUTATION TESTING")
    print("=" * 80)
    print()

    print("1. Sequence validation (split number must continue sequence)")
    test_mutation(
        "sequence-validation",
        "and not _section_continues_sequence(numbering, last_section)",
        "and not True  # MUTATION: disabled sequence check",
    )
    print()

    print("2. Top-level cap (jump <= 2)")
    test_mutation(
        "top-level-cap",
        "MAX_TOP_LEVEL_JUMP = 2",
        "MAX_TOP_LEVEL_JUMP = 100  # MUTATION: allow big jumps",
    )
    print()

    print("3. Percent (%) guard")
    test_mutation(
        "percent-guard",
        'if re.search(r"[%=]", title):',
        'if re.search(r"[NEVER]", title):  # MUTATION: disabled % guard',
    )
    print()

    print("4. Equals (=) guard (same pattern as %)")
    print("  (Covered by same regex as % guard)")
    print()

    print("5. Colon-digit (: <digit>) guard")
    test_mutation(
        "colon-digit-guard",
        'if re.search(r":\\s*[\\d.-]", title):',
        'if re.search(r"NEVER_MATCH", title):  # MUTATION: disabled : guard',
    )
    print()

    print("6. Short title (<3 chars) guard")
    test_mutation(
        "short-title-guard",
        "if len(title) < 3:",
        "if len(title) < 0:  # MUTATION: allow short titles",
    )
    print()

    print("7. Table/Figure prefix guard")
    test_mutation(
        "table-figure-guard",
        'r"^(?:Table|Figure|Fig\.|Algorithm|Eq\.|Equation|Appendix)\\s+\\d"',
        'r"^(?:NEVER_MATCH)\\s+\\d"  # MUTATION: disabled prefix guard',
    )
    print()

    print("8. Unnumbered heading exclusion from sequence")
    test_mutation(
        "unnumbered-exclusion",
        "if is_split_numbered or is_inline_numbered:",
        "if True:  # MUTATION: always set last_section",
    )
    print()

    print("9. Content guards scope (only for split numbers)")
    test_mutation(
        "content-guards-scope",
        "apply_content_guards=True,",
        "apply_content_guards=False,  # MUTATION: disable guards for split",
    )
    print()

    print("=" * 80)
    print("MUTATION TESTING COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
