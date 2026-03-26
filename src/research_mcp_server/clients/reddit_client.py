"""Reddit API client.

Supports two modes:
1. Authenticated (OAuth2): Set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET env vars.
   Higher rate limits (60 req/min), access to search.
2. Unauthenticated (public JSON): No env vars needed. Lower limits, works for browsing.

Target subreddits for dev/ML: MachineLearning, LocalLLaMA, programming,
devops, ExperiencedDevs, golang, rust, Python, node, nextjs, selfhosted, opensource
"""

import logging
import os
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import reddit_limiter

logger = logging.getLogger("research-mcp-server")

REDDIT_OAUTH_URL = "https://oauth.reddit.com"
REDDIT_PUBLIC_URL = "https://www.reddit.com"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# Curated dev subreddits for default searches
DEV_SUBREDDITS = [
    "MachineLearning", "LocalLLaMA", "programming", "devops",
    "ExperiencedDevs", "golang", "rust", "Python", "node",
    "nextjs", "selfhosted", "opensource", "webdev", "artificial",
]


def _normalize_post(post: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Reddit post (either from OAuth or public JSON)."""
    data = post.get("data", post)  # Public JSON wraps in 'data'
    return {
        "id": data.get("id", ""),
        "source": "reddit",
        "subreddit": data.get("subreddit", ""),
        "title": data.get("title", ""),
        "selftext": (data.get("selftext") or "")[:500],  # Truncate
        "url": data.get("url", ""),
        "author": data.get("author", ""),
        "score": data.get("score", 0),
        "upvote_ratio": data.get("upvote_ratio", 0),
        "num_comments": data.get("num_comments", 0),
        "created_utc": data.get("created_utc", 0),
        "permalink": f"https://reddit.com{data.get('permalink', '')}",
        "is_self": data.get("is_self", False),
        "link_flair_text": data.get("link_flair_text", ""),
    }


def _normalize_comment(comment: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Reddit comment."""
    data = comment.get("data", comment)
    return {
        "id": data.get("id", ""),
        "author": data.get("author", ""),
        "body": (data.get("body") or "")[:1000],  # Truncate
        "score": data.get("score", 0),
        "created_utc": data.get("created_utc", 0),
    }


class RedditClient:
    """Async Reddit client with OAuth2 or public JSON fallback."""

    def __init__(self) -> None:
        self._client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
        self._client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
        self._access_token: Optional[str] = None
        self._authenticated = bool(self._client_id and self._client_secret)

        if self._authenticated:
            logger.info("Reddit client initialized with OAuth2 credentials")
        else:
            logger.info("Reddit client using public JSON endpoints (no auth)")

    async def _get_token(self) -> str:
        """Get OAuth2 access token (app-only auth)."""
        if self._access_token:
            return self._access_token

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                REDDIT_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
                headers={"User-Agent": "research-mcp-server/1.0"},
            )
            resp.raise_for_status()
            self._access_token = resp.json()["access_token"]
            return self._access_token

    async def _request(self, path: str, params: dict | None = None) -> Any:
        """Make authenticated or public request."""
        await reddit_limiter.wait()

        headers = {"User-Agent": "research-mcp-server/1.0 (async)"}

        if self._authenticated:
            token = await self._get_token()
            headers["Authorization"] = f"Bearer {token}"
            url = f"{REDDIT_OAUTH_URL}{path}"
        else:
            url = f"{REDDIT_PUBLIC_URL}{path}.json"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params or {}, headers=headers)
            if resp.status_code == 401 and self._authenticated:
                # Token expired, retry
                self._access_token = None
                token = await self._get_token()
                headers["Authorization"] = f"Bearer {token}"
                resp = await client.get(url, params=params or {}, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def search(
        self,
        query: str,
        subreddit: str | None = None,
        sort: str = "relevance",
        time_filter: str = "month",
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search Reddit posts."""
        params = {
            "q": query,
            "sort": sort,
            "t": time_filter,
            "limit": min(max_results, 25),
            "type": "link",
        }

        if subreddit:
            path = f"/r/{subreddit}/search"
            params["restrict_sr"] = "on"
        else:
            path = "/search"

        data = await self._request(path, params)

        # Handle different response formats
        if isinstance(data, dict):
            children = data.get("data", {}).get("children", [])
        elif isinstance(data, list):
            children = data[0].get("data", {}).get("children", []) if data else []
        else:
            children = []

        return [_normalize_post(child) for child in children[:max_results]]

    async def trending(
        self,
        subreddits: list[str] | None = None,
        sort: str = "hot",
        max_results: int = 15,
    ) -> list[dict[str, Any]]:
        """Get trending posts from dev subreddits."""
        subs = subreddits or DEV_SUBREDDITS[:5]  # Default to top 5
        combined = "+".join(subs)

        data = await self._request(f"/r/{combined}/{sort}", {
            "limit": min(max_results, 25),
        })

        children = data.get("data", {}).get("children", [])
        return [_normalize_post(child) for child in children[:max_results]]

    async def get_discussion(
        self,
        post_id: str,
        subreddit: str | None = None,
        sort: str = "best",
        max_comments: int = 20,
    ) -> dict[str, Any]:
        """Get a post's top comments."""
        if subreddit:
            path = f"/r/{subreddit}/comments/{post_id}"
        else:
            path = f"/comments/{post_id}"

        data = await self._request(path, {
            "sort": sort,
            "limit": min(max_comments, 50),
            "depth": 2,
        })

        # Reddit returns [post_listing, comments_listing]
        post_data = {}
        comments = []

        if isinstance(data, list) and len(data) >= 2:
            post_children = data[0].get("data", {}).get("children", [])
            if post_children:
                post_data = _normalize_post(post_children[0])

            comment_children = data[1].get("data", {}).get("children", [])
            for child in comment_children:
                if child.get("kind") == "t1":  # Comment type
                    comments.append(_normalize_comment(child))

        return {
            "post": post_data,
            "top_comments": comments[:max_comments],
        }
