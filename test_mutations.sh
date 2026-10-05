#!/bin/bash
set -e
cd /workspace

echo "=== MUTATION TABLE ==="
echo ""
echo "Testing each mechanism alone by removing (skipping) the other mechanisms..."
echo ""

# Helper function to run a test with env vars and capture result
run_test() {
    local test_name="$1"
    local env_vars="$2"
    local expected_result="$3"  # "PASS" or "FAIL"
    
    echo -n "Testing: $test_name ... "
    
    set +e
    if [ -z "$env_vars" ]; then
        timeout 30 uv run pytest "tests/tools/test_metadata_deadline_integration.py::$test_name" -q --tb=no > /tmp/mutation_output.txt 2>&1
    else
        timeout 30 env $env_vars uv run pytest "tests/tools/test_metadata_deadline_integration.py::$test_name" -q --tb=no > /tmp/mutation_output.txt 2>&1
    fi
    result=$?
    set -e
    
    if [ $result -eq 0 ]; then
        actual="PASS"
    else
        actual="FAIL"
    fi
    
    if [ "$actual" = "$expected_result" ]; then
        echo "✓ $actual (expected $expected_result)"
    else
        echo "✗ $actual (expected $expected_result)"
        echo "Output:"
        cat /tmp/mutation_output.txt | tail -10
    fi
    
    # Extract timing if available
    timing=$(grep -o "took [0-9.]*s" /tmp/mutation_output.txt | head -1 || echo "")
    if [ -n "$timing" ]; then
        echo "  Timing: $timing"
    fi
    
    return 0
}

echo "## 1. Socket timeout mechanism alone"
echo "Baseline (all three mechanisms enabled):"
run_test "test_socket_timeout_mechanism_alone_enforces_deadline" "" "PASS"

echo ""
echo "Mutation: Remove socket timeout (skip it):"
run_test "test_socket_timeout_mechanism_alone_enforces_deadline" "_ARXIV_MCP_TEST_SKIP_SOCKET_TIMEOUT=1" "FAIL"

echo ""
echo "## 2. Watchdog mechanism alone"
echo "Baseline (all three mechanisms enabled):"
run_test "test_watchdog_mechanism_alone_enforces_deadline" "" "PASS"

echo ""
echo "Mutation: Remove watchdog (skip it):"
run_test "test_watchdog_mechanism_alone_enforces_deadline" "_ARXIV_MCP_TEST_SKIP_WATCHDOG=1" "FAIL"

echo ""
echo "## 3. Per-byte check mechanism alone"
echo "Baseline (all three mechanisms enabled):"
run_test "test_per_byte_check_alone_enforces_deadline" "" "PASS"

echo ""
echo "Mutation: Remove per-byte check (skip it):"
run_test "test_per_byte_check_alone_enforces_deadline" "_ARXIV_MCP_TEST_SKIP_PERBYTE_CHECK=1" "FAIL"

echo ""
echo "## 4. Socket unavailable fallback (thread backstop)"
echo "Baseline (thread backstop enabled):"
run_test "test_socket_unavailable_fallback_within_deadline" "" "PASS"

echo ""
echo "Mutation: Remove thread backstop (skip it):"
run_test "test_socket_unavailable_fallback_within_deadline" "_ARXIV_MCP_TEST_SKIP_THREAD_BACKSTOP=1" "FAIL"

echo ""
echo "## 5. All three mechanisms disabled (should stall)"
echo "Test socket timeout with all disabled:"
run_test "test_socket_timeout_mechanism_alone_enforces_deadline" "_ARXIV_MCP_TEST_SKIP_SOCKET_TIMEOUT=1 _ARXIV_MCP_TEST_SKIP_WATCHDOG=1 _ARXIV_MCP_TEST_SKIP_PERBYTE_CHECK=1" "FAIL"

echo ""
echo "=== MUTATION TABLE COMPLETE ==="
