"""Recent headlines for a stock from Google News RSS (free, no key)."""
from __future__ import annotations

from urllib.parse import quote_plus


def fetch_headlines(query: str, limit: int = 10) -> list[dict]:
    import feedparser

    url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(f"{query} stock NSE when:7d")
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    )
    feed = feedparser.parse(url)
    return [
        {
            "title": e.get("title", ""),
            "source": (e.get("source") or {}).get("title", ""),
            "published": e.get("published", ""),
            "link": e.get("link", ""),
        }
        for e in feed.entries[:limit]
    ]
