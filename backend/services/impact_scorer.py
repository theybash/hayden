import json
import re
from typing import List
from openai import OpenAI
from models.news import NewsItem
import config

BATCH_SIZE = 50


def _score_batch(client: OpenAI, batch: List[NewsItem]) -> None:
    """Score a batch and rewrite headlines into plain English."""
    headlines = []
    for i, item in enumerate(batch):
        symbol_str = f" [{item.symbol}]" if item.symbol else ""
        # For NSE items, include raw_data for context
        extra = ""
        if item.source == "nse" and item.raw_data:
            desc = item.raw_data.get("desc", "")
            att_text = item.raw_data.get("attchmntText", "")
            if desc:
                extra += f" | Filing: {desc[:100]}"
            if att_text:
                extra += f" | Details: {att_text[:100]}"
        headlines.append(f"{i+1}. {item.headline[:200]}{symbol_str}{extra}")

    headlines_text = "\n".join(headlines)

    try:
        response = client.chat.completions.create(
            model=config.SCORING_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a financial news analyst for Indian stock markets. "
                        "For each news item, provide TWO things:\n"
                        "1. impact_score (0-100) for market impact\n"
                        "2. title — a clear, plain-English one-line headline that tells the reader WHAT actually happened. "
                        "Strip all regulatory jargon. Be specific and direct.\n\n"
                        "Scoring guide:\n"
                        "  0-20: Routine, no price impact (AGM notices, routine filings)\n"
                        "  21-40: Minor news, small potential impact (board meeting dates)\n"
                        "  41-60: Moderate, could move stock 2-5% (quarterly results, mgmt changes)\n"
                        "  61-80: Significant, likely 5-10% move (major M&A, regulatory actions)\n"
                        "  81-100: Critical, 10%+ move (fraud, takeover, massive earnings surprise)\n\n"
                        "Return ONLY a JSON array of objects: [{\"score\": 45, \"title\": \"...\"}, ...]\n"
                        "Same order as input. No other text."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Score and rewrite these {len(batch)} news items:\n\n{headlines_text}",
                },
            ],
        )

        raw_text = (response.choices[0].message.content or "").strip()

        # Strip markdown fences
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_text = "\n".join(lines)

        results = json.loads(raw_text)

        if isinstance(results, list) and len(results) == len(batch):
            for item, r in zip(batch, results):
                if isinstance(r, dict):
                    item.impact_score = max(0, min(100, int(r.get("score", 0))))
                    title = r.get("title", "").strip()
                    if title:
                        item.headline = title
                elif isinstance(r, (int, float)):
                    # Fallback: old format (just scores)
                    item.impact_score = max(0, min(100, int(r)))
        else:
            print(f"[Scorer] Batch mismatch: got {len(results)}, expected {len(batch)}")

    except Exception as e:
        print(f"[Scorer] Batch failed: {e}")
        # Regex fallback — at least try to get scores
        try:
            numbers = re.findall(r"\d+", raw_text)
            if len(numbers) >= len(batch):
                for item, s in zip(batch, numbers[:len(batch)]):
                    item.impact_score = max(0, min(100, int(s)))
        except Exception:
            pass


def score_items(items: List[NewsItem]) -> List[NewsItem]:
    """Score news items 0-100 for market impact using LLM, in batches."""
    if not items:
        return items

    if not config.OPENAI_API_KEY:
        print("[Scorer] No OpenAI API key, returning unscored items")
        return items

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"[Scorer] Scoring {len(items)} items in {total_batches} batches...")

    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        print(f"[Scorer] Batch {batch_num}/{total_batches} ({len(batch)} items)")
        _score_batch(client, batch)

    return items
