#!/bin/bash
# Mutation testing: disable each guard/rule and verify tests fail

cd /workspace
export PATH="$HOME/.local/bin:$PATH"

echo "===================================================================="
echo "MUTATION TESTING"
echo "===================================================================="
echo ""

# Backup original file
cp src/arxiv_mcp_server/tools/paper_outline.py src/arxiv_mcp_server/tools/paper_outline.py.backup

run_test() {
    local name="$1"
    local pattern="$2"
    local replacement="$3"
    
    echo "Testing: $name"
    echo "  Disabling..."
    
    # Apply mutation
    sed -i "s/$pattern/$replacement/" src/arxiv_mcp_server/tools/paper_outline.py
    
    # Run tests
    result=$(uv run pytest tests/tools/test_paper_outline.py -q 2>&1 | tail -1)
    
    # Restore
    cp src/arxiv_mcp_server/tools/paper_outline.py.backup src/arxiv_mcp_server/tools/paper_outline.py
    
    if echo "$result" | grep -q "failed"; then
        echo "  ✓ Tests FAILED as expected"
        echo "  Result: $result"
    elif echo "$result" | grep -q "passed"; then
        echo "  ✗ Tests PASSED (guard not effective!)"
        echo "  Result: $result"
    else
        echo "  ? Unknown result: $result"
    fi
    echo ""
}

# Test 1: Sequence validation
echo "1. Sequence validation (top-level jump cap)"
run_test "sequence-validation" \
    "if not has_trailing_period and last_section is not None and not _section_continues_sequence(numbering, last_section):" \
    "if not has_trailing_period and last_section is not None and not True:"

# Test 2: Top-level cap (MAX_TOP_LEVEL_JUMP)
echo "2. Top-level cap (jump <= 2)"
run_test "top-level-cap" \
    "MAX_TOP_LEVEL_JUMP = 2" \
    "MAX_TOP_LEVEL_JUMP = 100"

# Test 3: Percent guard
echo "3. Percent (%) guard"
run_test "percent-guard" \
    'if re.search(r"\[%=\]", title):' \
    'if re.search(r"\[NEVER_MATCH\]", title):'

# Test 4: Equals (=) guard
echo "4. Equals (=) guard"
run_test "equals-guard" \
    'if re.search(r"\[%=\]", title):' \
    'if re.search(r"\[%NEVER\]", title):'

# Test 5: Colon-digit guard
echo "5. Colon-digit (: <digit>) guard"
run_test "colon-digit-guard" \
    'if re.search(r":\\\\s\*\[\\\\d.-\]", title):' \
    'if re.search(r"NEVER_MATCH", title):'

# Test 6: Short title guard (<3 chars)
echo "6. Short title (<3 chars) guard"
run_test "short-title-guard" \
    "if len(title) < 3:" \
    "if len(title) < 0:"

# Test 7: Table/Figure prefix guard
echo "7. Table/Figure prefix guard"
run_test "table-figure-guard" \
    'r"\^(?:Table\|Figure\|Fig\\\\.\|Algorithm\|Eq\\\\.\|Equation\|Appendix)\\\\s\+\\\\d"' \
    'r"\^(?:NEVER_MATCH)\\\\s\+\\\\d"'

# Test 8: Unnumbered heading exclusion from sequence
echo "8. Unnumbered heading exclusion"
echo "  (Abstract/References don't set last_section)"
run_test "unnumbered-exclusion" \
    "if is_split_numbered or is_inline_numbered:" \
    "if True:"  # Always set last_section

echo "===================================================================="
echo "MUTATION TESTING COMPLETE"
echo "===================================================================="

# Cleanup
rm -f src/arxiv_mcp_server/tools/paper_outline.py.backup
