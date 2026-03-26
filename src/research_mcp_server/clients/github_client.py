"""GitHub REST API client.

Base URL: https://api.github.com
Auth: Optional GITHUB_TOKEN for higher rate limits (5000 req/hr vs 60 req/hr).
"""

import logging
import os
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import github_limiter

logger = logging.getLogger("research-mcp-server")

GITHUB_API = "https://api.github.com"


def _normalize_repo(repo: dict[str, Any]) -> dict[str, Any]:
    """Normalize GitHub repo to standard format."""
    return {
        "name": repo.get("full_name", ""),
        "description": repo.get("description", ""),
        "url": repo.get("html_url", ""),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "open_issues": repo.get("open_issues_count", 0),
        "language": repo.get("language", ""),
        "topics": repo.get("topics", []),
        "license": (repo.get("license") or {}).get("spdx_id", ""),
        "created_at": repo.get("created_at", ""),
        "updated_at": repo.get("updated_at", ""),
        "pushed_at": repo.get("pushed_at", ""),
        "watchers": repo.get("watchers_count", 0),
        "default_branch": repo.get("default_branch", "main"),
        "archived": repo.get("archived", False),
        "source": "github",
    }


def _normalize_release(release: dict[str, Any], repo_name: str = "") -> dict[str, Any]:
    """Normalize GitHub release."""
    return {
        "repo": repo_name,
        "tag": release.get("tag_name", ""),
        "name": release.get("name", ""),
        "published_at": release.get("published_at", ""),
        "prerelease": release.get("prerelease", False),
        "draft": release.get("draft", False),
        "body": (release.get("body") or "")[:500],  # Truncate long release notes
        "url": release.get("html_url", ""),
        "author": (release.get("author") or {}).get("login", ""),
    }


class GitHubClient:
    """Async client for GitHub REST API."""

    def __init__(self) -> None:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        self._headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
            logger.info("GitHub client initialized with auth token")
        else:
            logger.info("GitHub client initialized without auth (60 req/hr limit)")

    async def _request(self, path: str, params: dict | None = None) -> Any:
        await github_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{GITHUB_API}{path}",
                params=params or {},
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def search_repos(
        self,
        query: str,
        sort: str = "stars",
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search GitHub repositories."""
        data = await self._request("/search/repositories", {
            "q": query,
            "sort": sort,
            "order": "desc",
            "per_page": min(max_results, 30),
        })
        return [_normalize_repo(r) for r in data.get("items", [])]

    async def get_repo(self, owner_repo: str) -> dict[str, Any]:
        """Get detailed repo info. owner_repo = 'owner/repo'."""
        repo = await self._request(f"/repos/{owner_repo}")
        result = _normalize_repo(repo)

        # Fetch additional stats
        try:
            # Contributors count (use per_page=1 and check headers)
            await github_limiter.wait()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API}/repos/{owner_repo}/contributors",
                    params={"per_page": 1, "anon": "true"},
                    headers=self._headers,
                )
                # GitHub returns Link header with last page number
                link = resp.headers.get("Link", "")
                if 'rel="last"' in link:
                    import re
                    match = re.search(r'page=(\d+)>; rel="last"', link)
                    result["contributors_count"] = int(match.group(1)) if match else len(resp.json())
                else:
                    result["contributors_count"] = len(resp.json()) if resp.status_code == 200 else None
        except Exception:
            result["contributors_count"] = None

        return result

    async def get_releases(
        self, owner_repo: str, max_results: int = 5
    ) -> list[dict[str, Any]]:
        """Get recent releases for a repo."""
        releases = await self._request(
            f"/repos/{owner_repo}/releases",
            {"per_page": min(max_results, 10)},
        )
        return [_normalize_release(r, owner_repo) for r in releases]

    async def compare_repos(
        self, repos: list[str]
    ) -> list[dict[str, Any]]:
        """Get info for multiple repos for comparison."""
        results = []
        for owner_repo in repos:
            try:
                info = await self.get_repo(owner_repo)
                results.append(info)
            except Exception as e:
                results.append({
                    "name": owner_repo,
                    "error": str(e),
                    "source": "github",
                })
        return results

    async def trending(
        self,
        language: str | None = None,
        since: str = "weekly",
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Get trending repos using GitHub search API (sorted by stars, created recently).

        GitHub doesn't have an official trending API, so we approximate
        with search: repos created in last week/month sorted by stars.
        """
        from datetime import datetime, timedelta

        since_map = {"daily": 1, "weekly": 7, "monthly": 30}
        days = since_map.get(since, 7)
        date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        query = f"created:>{date_from}"
        if language:
            query += f" language:{language}"
        query += " stars:>10"

        data = await self._request("/search/repositories", {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max_results, 30),
        })
        return [_normalize_repo(r) for r in data.get("items", [])]
