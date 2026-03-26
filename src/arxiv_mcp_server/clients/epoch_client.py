"""Epoch AI data client for AI model benchmarks and capabilities tracking.

Epoch AI provides public data on AI model capabilities via CSV datasets:
    - Notable AI models: https://epoch.ai/data/epochdb/notable_ai_models.csv
    - Benchmark runs: https://epoch.ai/data/epochdb/benchmark_runs.csv

No formal API — downloads and caches CSVs locally. The CSVs are large;
cache them to avoid re-downloading on every request. Cache expires after 24h.
"""

import csv
import io
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import Settings

logger = logging.getLogger("arxiv-mcp-server")

EPOCH_MODELS_URL = "https://epoch.ai/data/epochdb/notable_ai_models.csv"
EPOCH_BENCHMARKS_URL = "https://epoch.ai/data/epochdb/benchmark_runs.csv"

CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class EpochClient:
    """Async client for Epoch AI's public CSV datasets."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir is None:
            settings = Settings()
            cache_dir = settings.STORAGE_PATH / "epoch_cache"
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def _download_csv(self, url: str, filename: str) -> list[dict[str, str]]:
        """Download and cache a CSV file. Reuse cache if less than 24h old.

        Args:
            url: URL of the CSV file to download.
            filename: Local filename for the cached copy.

        Returns:
            List of dicts, one per CSV row.
        """
        cache_path = self._cache_dir / filename
        now = time.time()

        # Check if cached file exists and is fresh
        if cache_path.exists():
            age = now - cache_path.stat().st_mtime
            if age < CACHE_TTL_SECONDS:
                logger.debug(f"Using cached {filename} (age: {age:.0f}s)")
                text = cache_path.read_text(encoding="utf-8")
                reader = csv.DictReader(io.StringIO(text))
                return list(reader)

        # Download fresh copy
        logger.info(f"Downloading {url} -> {cache_path}")
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

        text = response.text
        cache_path.write_text(text, encoding="utf-8")

        reader = csv.DictReader(io.StringIO(text))
        return list(reader)

    async def get_models(
        self,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search notable AI models from Epoch's catalog.

        Args:
            query: Search term to filter by model name or organization.
            limit: Maximum number of results to return.

        Returns:
            List of model dicts with fields like model_name, organization,
            year, parameters, training_compute, domain, etc.
        """
        rows = await self._download_csv(EPOCH_MODELS_URL, "notable_ai_models.csv")

        if query:
            query_lower = query.lower()
            rows = [
                r for r in rows
                if query_lower in r.get("System", "").lower()
                or query_lower in r.get("Organization", "").lower()
                or query_lower in r.get("Domain", "").lower()
            ]

        results: list[dict[str, Any]] = []
        for row in rows[:limit]:
            results.append({
                "model_name": row.get("System", ""),
                "organization": row.get("Organization", ""),
                "year": row.get("Publication date", ""),
                "parameters": row.get("Parameters", ""),
                "training_compute": row.get("Training compute (FLOP)", ""),
                "domain": row.get("Domain", ""),
                "task": row.get("Task", ""),
                "training_dataset_size": row.get("Training dataset size (datapoints)", ""),
                "country": row.get("Country (from Organization)", ""),
                "link": row.get("Link", ""),
            })

        return results

    async def get_benchmark_runs(
        self,
        model: Optional[str] = None,
        benchmark: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search benchmark results from Epoch's dataset.

        Args:
            model: Filter by model name (case-insensitive substring match).
            benchmark: Filter by benchmark name (case-insensitive substring match).
            limit: Maximum number of results to return.

        Returns:
            List of benchmark run dicts.
        """
        rows = await self._download_csv(EPOCH_BENCHMARKS_URL, "benchmark_runs.csv")

        if model:
            model_lower = model.lower()
            rows = [
                r for r in rows
                if model_lower in r.get("System", "").lower()
                or model_lower in r.get("model", "").lower()
            ]

        if benchmark:
            benchmark_lower = benchmark.lower()
            rows = [
                r for r in rows
                if benchmark_lower in r.get("Benchmark", "").lower()
                or benchmark_lower in r.get("benchmark", "").lower()
            ]

        results: list[dict[str, Any]] = []
        for row in rows[:limit]:
            results.append({
                "model": row.get("System", row.get("model", "")),
                "benchmark": row.get("Benchmark", row.get("benchmark", "")),
                "score": row.get("Score", row.get("score", "")),
                "date": row.get("Date", row.get("date", "")),
                "organization": row.get("Organization", row.get("organization", "")),
                "notes": row.get("Notes", row.get("notes", "")),
            })

        return results

    async def compare_models(
        self,
        model_names: list[str],
    ) -> dict[str, Any]:
        """Get benchmark data for specific models side by side.

        Args:
            model_names: List of model names to compare.

        Returns:
            Dict with model info and benchmark results grouped per model.
        """
        models_data = await self._download_csv(
            EPOCH_MODELS_URL, "notable_ai_models.csv"
        )
        benchmarks_data = await self._download_csv(
            EPOCH_BENCHMARKS_URL, "benchmark_runs.csv"
        )

        comparison: dict[str, Any] = {}

        for name in model_names:
            name_lower = name.lower()

            # Find model info
            model_info: Optional[dict[str, Any]] = None
            for row in models_data:
                if name_lower in row.get("System", "").lower():
                    model_info = {
                        "model_name": row.get("System", ""),
                        "organization": row.get("Organization", ""),
                        "year": row.get("Publication date", ""),
                        "parameters": row.get("Parameters", ""),
                        "training_compute": row.get("Training compute (FLOP)", ""),
                        "domain": row.get("Domain", ""),
                    }
                    break

            # Find benchmark runs for this model
            model_benchmarks: list[dict[str, Any]] = []
            for row in benchmarks_data:
                sys_name = row.get("System", row.get("model", ""))
                if name_lower in sys_name.lower():
                    model_benchmarks.append({
                        "benchmark": row.get("Benchmark", row.get("benchmark", "")),
                        "score": row.get("Score", row.get("score", "")),
                        "date": row.get("Date", row.get("date", "")),
                    })

            comparison[name] = {
                "info": model_info,
                "benchmarks": model_benchmarks,
            }

        return comparison
