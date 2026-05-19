import json
import hashlib
from datetime import datetime
from typing import List
from openai import OpenAI
from models.news import NewsItem
import config


def fetch_web_news() -> List[NewsItem]:
    """Use GPT-5.2 with web search to find Indian stock market news."""
    if not config.OPENAI_API_KEY:
        print("[WebNews] No OpenAI API key configured, skipping web news")
        return []

    client = OpenAI(api_key=config.OPENAI_API_KEY)

    try:
        response = client.responses.create(
            model=config.NEWS_MODEL,
            tools=[{"type": "web_search_preview"}],
            instructions=(
                "You are a financial news aggregator focused on Indian stock markets (NSE/BSE). "
                "Search for the most recent and significant news."
            ),
            input=(
                "Search for the latest Indian stock market news from today. "
                "Find 10-15 significant news items including corporate announcements, "
                "earnings results, insider trading, regulatory changes, and market-moving events. "
                "For each item return a JSON array with objects containing: "
                "headline (string), source_name (string), url (string), "
                "symbol (string or null if not about a specific stock), "
                "timestamp_hint (ISO format string or null). "
                "Return ONLY a valid JSON array, no markdown fences, no other text."
            ),
        )

        raw_text = response.output_text.strip()

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_text = "\n".join(lines)

        news_data = json.loads(raw_text)
        if not isinstance(news_data, list):
            print("[WebNews] LLM did not return a list")
            return []

    except Exception as e:
        print(f"[WebNews] Failed to fetch web news: {e}")
        return []

    items = []
    for d in news_data:
        headline = d.get("headline", "")
        if not headline:
            continue

        raw_id = f"web_{headline}_{d.get('source_name', '')}"
        item_id = hashlib.md5(raw_id.encode()).hexdigest()[:12]

        # Parse timestamp
        ts_hint = d.get("timestamp_hint")
        try:
            ts = datetime.fromisoformat(ts_hint) if ts_hint else datetime.now()
        except (ValueError, TypeError):
            ts = datetime.now()

        items.append(NewsItem(
            id=item_id,
            headline=headline,
            source="web",
            url=d.get("url"),
            timestamp=ts,
            impact_score=0,  # scored later
            symbol=d.get("symbol"),
        ))

    return items
