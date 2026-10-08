# Changelog

All notable changes to arxiv-mcp-server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.2] - 2026-10-08

### Fixed

#### Citation cache metadata leak
- Request metadata (query text, max results, sort order, field filters) no longer leaks into the citation graph cache ([PR #290](https://github.com/blazickjp/arxiv-mcp-server/pull/290), [#289](https://github.com/blazickjp/arxiv-mcp-server/pull/289))
- Previously, the first citation graph call would cache results under keys that incorrectly included search request metadata
- Citation lookups now use clean cache keys derived only from paper identifiers

#### Appendix navigation after References
- Appendices that appear after the References or Bibliography section are now included in the paper outline and can be read by title or index ([PR #291](https://github.com/blazickjp/arxiv-mcp-server/pull/291), [#288](https://github.com/blazickjp/arxiv-mcp-server/issues/288))
- The References section now ends at the next recognized appendix, not at the end of the document
- Section lookup by bare title (e.g., "Appendix A") now returns an error listing all candidate sections when the title is ambiguous
- Ambiguity detection prevents silent failures when multiple sections share the same bare title

#### Appendix title recognition
- Appendix titles containing the lowercase word "that" (e.g., "Appendix A: Theorems that enable our approach") are now correctly recognized ([PR #292](https://github.com/blazickjp/arxiv-mcp-server/pull/292))
- Previous regex required title-case "That" and rejected valid appendix headings

### Changed

#### Section ID stability
- Section IDs can shift for papers with appendices after References
- Example: on arXiv paper 2305.04388, section IDs from Appendix C onward move by +1
- Clients that cached section IDs should re-fetch the outline after upgrading

### Known Limitations
- Appendices that appear before the References section remain out of scope for outline parsing
- Markdown heading syntax (`#`) from prompt templates in DeepSeek-R1 papers can still be incorrectly recognized as section boundaries

Related: [PR #290](https://github.com/blazickjp/arxiv-mcp-server/pull/290), [PR #291](https://github.com/blazickjp/arxiv-mcp-server/pull/291), [PR #292](https://github.com/blazickjp/arxiv-mcp-server/pull/292), [Issue #288](https://github.com/blazickjp/arxiv-mcp-server/issues/288), [Issue #289](https://github.com/blazickjp/arxiv-mcp-server/issues/289)

## [0.8.1] - 2026-10-05

### Fixed

Three critical bugs from issue [#284](https://github.com/blazickjp/arxiv-mcp-server/issues/284):

#### Metadata deadline enforcement
- Socket timeout properly enforced during metadata requests
- Watchdog timer implemented to catch stalled operations
- Per-byte read timeout added
- Windows thread-abandon and unreachable-socket backstop added
- Metadata requests now respect configured time limits and total deadlines
- Optional metadata can be skipped when time budget is exhausted

#### arxiv_version restoration
- Restored `arxiv_version` tracking (refs [#206](https://github.com/blazickjp/arxiv-mcp-server/issues/206))
- Prevents version downgrade protection issues

#### Rate-limit gate behavior
- Rate-limit gate now properly released after request initiation
- Prevents blocking subsequent requests unnecessarily

#### Dependencies
- Declared `lxml` and `requests` as explicit dependencies in pyproject.toml
- Ensures all required packages are properly installed

#### Paper outline parsing
- Hardened HTML outline sequence check
- Improved section boundary detection for papers with split numbering (e.g., "2.2\nKAN architecture")

Related: [PR #285](https://github.com/blazickjp/arxiv-mcp-server/pull/285), [Issue #284](https://github.com/blazickjp/arxiv-mcp-server/issues/284)

## [0.8.0] - 2026-10-03

### Changed

#### Timeout/retry hardening
- Every call is now capped by `ARXIV_MAX_TOTAL_TIME` (default 50s) to enforce a per-call time budget
- HTTP 429/503 retries reduced from 6 calls to 3 for feed queries
- PDF 429/503 and connection errors now retry with exponential backoff
- `Retry-After` headers are honored; calls return `rate_limited` when `Retry-After` exceeds the backoff cap or remaining time budget
- HTTP 406 responses remain classified as `rate_limited` with no PDF fallback (refs #277)
- Deadline checks while streaming large responses to fail fast when the budget is exhausted

### Added
- New `ARXIV_*` timeout/retry environment variables for fine-tuning:
  - `ARXIV_MAX_TOTAL_TIME` (default 50s): per-call time budget including retries
  - `ARXIV_REQUEST_TIMEOUT`: HTTP request timeout

### Deprecated
- `REQUEST_TIMEOUT` is deprecated and now applies to all requests rather than just feed queries

Related: [PR #282](https://github.com/blazickjp/arxiv-mcp-server/pull/282), [Issue #281](https://github.com/blazickjp/arxiv-mcp-server/issues/281), [Issue #277](https://github.com/blazickjp/arxiv-mcp-server/issues/277)

[0.8.2]: https://github.com/blazickjp/arxiv-mcp-server/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/blazickjp/arxiv-mcp-server/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/blazickjp/arxiv-mcp-server/releases/tag/v0.8.0
