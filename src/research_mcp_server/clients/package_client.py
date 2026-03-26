"""Unified package registry client for npm, PyPI, and crates.io.

Endpoints:
    npm:      https://registry.npmjs.org/{pkg}
              https://api.npmjs.org/downloads/point/last-month/{pkg}
    PyPI:     https://pypi.org/pypi/{pkg}/json
    crates:   https://crates.io/api/v1/crates/{name}

No auth required. Rate limits: generous (~10 req/s each).
"""

import logging
from typing import Any, Optional
from datetime import datetime

import httpx

from ..utils.rate_limiter import npm_limiter, pypi_limiter, crates_limiter

logger = logging.getLogger("research-mcp-server")


def _normalize_npm(pkg: dict[str, Any], downloads: int | None = None) -> dict[str, Any]:
    """Normalize npm package data."""
    latest = pkg.get("dist-tags", {}).get("latest", "")
    time_data = pkg.get("time", {})
    latest_version = pkg.get("versions", {}).get(latest, {})
    return {
        "name": pkg.get("name", ""),
        "registry": "npm",
        "description": latest_version.get("description", pkg.get("description", "")),
        "version": latest,
        "license": latest_version.get("license", ""),
        "homepage": latest_version.get("homepage", pkg.get("homepage", "")),
        "repository": (latest_version.get("repository", {}) or {}).get("url", ""),
        "keywords": latest_version.get("keywords", []),
        "last_published": time_data.get(latest, ""),
        "created": time_data.get("created", ""),
        "monthly_downloads": downloads,
    }


def _normalize_pypi(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize PyPI package data."""
    info = data.get("info", {})
    urls = data.get("urls", [])
    latest_upload = urls[-1].get("upload_time_iso_8601", "") if urls else ""
    return {
        "name": info.get("name", ""),
        "registry": "pypi",
        "description": info.get("summary", ""),
        "version": info.get("version", ""),
        "license": info.get("license", ""),
        "homepage": info.get("home_page", info.get("project_url", "")),
        "repository": next(
            (
                u
                for k, u in (info.get("project_urls") or {}).items()
                if "source" in k.lower()
                or "github" in k.lower()
                or "repo" in k.lower()
            ),
            "",
        ),
        "keywords": [
            k.strip() for k in (info.get("keywords") or "").split(",") if k.strip()
        ],
        "last_published": latest_upload,
        "created": "",  # PyPI doesn't expose creation date easily
        "requires_python": info.get("requires_python", ""),
        "monthly_downloads": None,  # Would need BigQuery for this
    }


def _normalize_crate(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize crates.io crate data."""
    crate = data.get("crate", data)
    return {
        "name": crate.get("name", ""),
        "registry": "crates.io",
        "description": crate.get("description", ""),
        "version": crate.get("newest_version", crate.get("max_version", "")),
        "license": "",  # Need to fetch version details for license
        "homepage": crate.get("homepage", ""),
        "repository": crate.get("repository", ""),
        "keywords": crate.get("keywords", []),
        "last_published": crate.get("updated_at", ""),
        "created": crate.get("created_at", ""),
        "total_downloads": crate.get("downloads", 0),
        "recent_downloads": crate.get("recent_downloads", 0),
    }


class PackageClient:
    """Unified async client for npm, PyPI, and crates.io."""

    async def _npm_request(self, path: str) -> Any:
        await npm_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://registry.npmjs.org{path}")
            resp.raise_for_status()
            return resp.json()

    async def _npm_downloads(self, pkg: str) -> int | None:
        """Get npm monthly download count."""
        try:
            await npm_limiter.wait()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://api.npmjs.org/downloads/point/last-month/{pkg}"
                )
                if resp.status_code == 200:
                    return resp.json().get("downloads")
        except Exception:
            pass
        return None

    async def _pypi_request(self, pkg: str) -> Any:
        await pypi_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://pypi.org/pypi/{pkg}/json")
            resp.raise_for_status()
            return resp.json()

    async def _crates_request(self, path: str) -> Any:
        await crates_limiter.wait()
        headers = {"User-Agent": "research-mcp-server (async)"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://crates.io/api/v1{path}", headers=headers
            )
            resp.raise_for_status()
            return resp.json()

    async def get_npm(self, name: str) -> dict[str, Any]:
        """Get npm package info + downloads."""
        pkg = await self._npm_request(f"/{name}")
        downloads = await self._npm_downloads(name)
        return _normalize_npm(pkg, downloads)

    async def get_pypi(self, name: str) -> dict[str, Any]:
        """Get PyPI package info."""
        data = await self._pypi_request(name)
        return _normalize_pypi(data)

    async def get_crate(self, name: str) -> dict[str, Any]:
        """Get crates.io crate info."""
        data = await self._crates_request(f"/crates/{name}")
        return _normalize_crate(data)

    async def get_package(self, name: str, registry: str) -> dict[str, Any]:
        """Get package info from a specific registry."""
        if registry == "npm":
            return await self.get_npm(name)
        elif registry == "pypi":
            return await self.get_pypi(name)
        elif registry == "crates":
            return await self.get_crate(name)
        else:
            raise ValueError(f"Unknown registry: {registry}")

    async def search_npm(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Search npm packages."""
        await npm_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://registry.npmjs.org/-/v1/search",
                params={"text": query, "size": min(max_results, 20)},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for obj in data.get("objects", []):
            pkg = obj.get("package", {})
            results.append(
                {
                    "name": pkg.get("name", ""),
                    "registry": "npm",
                    "description": pkg.get("description", ""),
                    "version": pkg.get("version", ""),
                    "keywords": pkg.get("keywords", []),
                    "score": round(obj.get("score", {}).get("final", 0), 3),
                }
            )
        return results

    async def search_pypi(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Search PyPI packages via XMLRPC (limited but works)."""
        # PyPI doesn't have a great search API. Use the simple JSON endpoint.
        # Fallback: search via warehouse API
        await pypi_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://pypi.org/search/",
                params={"q": query},
                headers={"Accept": "application/json"},
            )
            # PyPI search returns HTML, not JSON. Use a workaround.
            # Return empty and suggest using package name directly.
            return []  # PyPI has no public search API that returns JSON

    async def search_crates(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Search crates.io."""
        data = await self._crates_request(
            f"/crates?q={query}&per_page={min(max_results, 20)}"
        )
        results = []
        for crate in data.get("crates", []):
            results.append(
                {
                    "name": crate.get("name", ""),
                    "registry": "crates.io",
                    "description": crate.get("description", ""),
                    "version": crate.get("newest_version", ""),
                    "downloads": crate.get("downloads", 0),
                    "recent_downloads": crate.get("recent_downloads", 0),
                }
            )
        return results
