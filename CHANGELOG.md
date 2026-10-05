# Changelog

All notable changes to arxiv-mcp-server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.8.1]: https://github.com/blazickjp/arxiv-mcp-server/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/blazickjp/arxiv-mcp-server/releases/tag/v0.8.0
