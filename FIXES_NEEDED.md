# Comprehensive Fixes Needed for PR #282

## Status: IN PROGRESS (Partial fixes committed)

## Completed Fixes (in arxiv_api.py)

✅ **Blocker 1**: Always enforce budget with `asyncio.wait_for`
- Changed from conditional (only when remaining < ARXIV_REQUEST_TIMEOUT) to always
- Every attempt now wrapped with remaining budget timeout

✅ **Blocker 2**: Catch `asyncio.TimeoutError` from wait_for
- Added handler that returns proper ArxivTimeoutError or ArxivRateLimitError
- No more empty error messages

✅ **Blocker 4**: Cap backoff sleep to remaining budget
- Added budget checks before all `asyncio.sleep()` calls
- Returns immediately if Retry-After > remaining budget with rate_limited response
- Retry-After now enforced as floor BEFORE jitter

✅ **Blocker 9**: Parse HTTP-date Retry-After
- Updated `_parse_retry_after_seconds()` to handle both integer and HTTP-date formats
- Uses `email.utils.parsedate_to_datetime()`

## Remaining Critical Fixes

### Blocker 3: URL Leaks in Log Messages
**Problem**: Log messages contain full URLs like "arXiv API request to https://export.arxiv.org/api/query?..."

**Fix needed**:
- Update all logger.warning/error calls to use clean names:
  - "arXiv API request" instead of "arXiv API request to https://..."  
  - "Semantic Scholar API" instead of URL
- Check: search.py `_rate_limited_get`, citation_graph.py `_s2_get`

### Blocker 5: HTML/PDF/LaTeX No Budget Enforcement
**Problem**: These paths don't use retry_with_backoff, can run for 373s

**Fix needed in download.py**:
- `_fetch_html_content_sync`: Wrap entire loop in budget check, pass to retry logic
- `_fetch_pdf_content`: Remove custom max(120, ...) timeout, use ARXIV_REQUEST_TIMEOUT
- `stream_pdf_to_path`: Apply budget enforcement

**Fix needed in latex.py**:
- `get_paper_latex`, `list_paper_latex_sections`, `get_paper_latex_section`
- Wrap HTTP requests with budget-aware retry

### Blocker 6: Lock Held Through Retries
**Problem**: `ARXIV_RATE_LIMITER.run()` holds lock through backoff sleeps

**Fix needed**:
- Release lock BEFORE backoff sleep
- Re-acquire before next attempt
- Each concurrent call should respect its own budget

**Suggested approach**:
```python
# In retry_with_backoff, before sleep:
# 1. Release any held lock
# 2. Sleep
# 3. Re-acquire lock before next operation()
```

### Blocker 7 (REGRESSION): HTML Timeout No Fallback
**Problem**: HTML timeout/connection error returns error instead of falling back to PDF

**Fix needed in download.py `handle_download`**:
- Catch timeout/connection errors from `_fetch_html_content`
- Fall back to existence check + PDF like main does
- Test: `test_html_timeout_fallback_to_pdf`

### Blocker 8 (REGRESSION): PDF Timeout Reported as rate_limited
**Problem**: PDF timeout gives `rate_limited` with `http_status: 0`

**Fix needed in download.py**:
- PDF timeout should raise ArxivTimeoutError, not ArxivRateLimitError
- Check `_fetch_pdf_content` error handling
- Test: `test_pdf_timeout_honest_error`

## Secondary Fixes

### Nit: Semantic Scholar Messages Say "arXiv"
- Update citation_graph.py error messages
- "Semantic Scholar API" not "arXiv"

### Nit: S2 503 Reported as "Quota Exhausted"
- 503 is service unavailable, not quota
- Update message

### Nit: HTML 406 Test Really Sleeps
- Mock asyncio.sleep in test_html_fetch_406_raises_rate_limit_error

### Nit: Missing Assertion
- Restore `assert "HTTP 406" in str(exc_info.value)` in test_html_fetch_406_raises_rate_limit_error

### Nit: PR Body Accuracy
- Not all tools use unified helper (LaTeX, export, alerts still custom)
- Call out PDF 429/503 now 3 calls (was 1), HTML 406 now 2 calls (was 1)

### Nit: No Tracebacks for Expected Failures
- Remove/suppress tracebacks for 429/503/406/timeout

## New Tests Needed

1. `test_trickling_body_cut_at_budget`: Slow-reading body terminated at budget
2. `test_budget_expiry_message_not_empty`: Message is populated and URL-free
3. `test_retry_after_exceeds_budget_immediate_return`: rate_limited without sleep
4. `test_http_date_retry_after_parsing`: Parse "Wed, 21 Oct 2026 07:00:00 GMT"
5. `test_html_timeout_fallback_to_pdf`: HTML timeout → existence check → PDF
6. `test_pdf_timeout_honest_error`: PDF timeout returns timeout error not rate_limited
7. `test_html_pdf_latex_respect_budget`: All finish within ARXIV_MAX_TOTAL_TIME
8. `test_concurrent_stalls_own_budgets`: Two concurrent calls each ~50s, not 50s+100s

## Testing Strategy

Create a tiny misbehaving HTTP server locally to reproduce stall scenarios:
```python
# Mock server that:
# - Stalls (never sends response)
# - Trickles body slowly (1 byte/sec)
# - Returns 429 with Retry-After: 120
# - Returns 429 with Retry-After: "Wed, 21 Oct 2026..."
```

Include before/after timing table in PR body.
