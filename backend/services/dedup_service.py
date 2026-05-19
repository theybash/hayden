import re
from difflib import SequenceMatcher
from typing import List
from models.news import NewsItem


def _normalize(text: str) -> str:
    """Normalize headline for comparison."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)  # remove punctuation
    text = re.sub(r"\s+", " ", text)  # collapse whitespace
    return text


def _headline_similarity(a: str, b: str) -> float:
    """Quick similarity check between two headlines."""
    na = _normalize(a)
    nb = _normalize(b)

    # Quick length check — very different lengths = likely different stories
    if len(na) == 0 or len(nb) == 0:
        return 0.0
    ratio = min(len(na), len(nb)) / max(len(na), len(nb))
    if ratio < 0.4:
        return 0.0

    return SequenceMatcher(None, na, nb).ratio()


# Source reputation for picking the "best" version when deduping
SOURCE_RANK = {
    "Reuters Business": 10, "Reuters Markets": 10,
    "ET Markets": 9, "ET Economy": 9,
    "MoneyControl Top": 8, "MoneyControl Markets": 8,
    "BS Markets": 8, "BS Economy": 8,
    "CNBC Top": 8, "CNBC World": 8,
    "BBC Business": 8, "BBC World": 7,
    "LiveMint Markets": 7, "LiveMint Economy": 7,
    "NDTV Profit": 7,
    "AP News": 7,
    "MarketWatch": 7,
    "Yahoo Finance": 6,
    "CNN Top": 6, "CNN Money": 6,
    "Fox Business": 6, "Fox News": 5,
    "RBI Press": 10, "PIB Economy": 9,
    "US Fed": 10, "ECB Press": 10, "IMF News": 9, "World Bank": 8,
    "CoinDesk": 7, "CoinTelegraph": 6,
}


def deduplicate(items: List[NewsItem]) -> List[NewsItem]:
    """Remove duplicates by URL and headline similarity."""
    if not items:
        return items

    # Stage 1: URL dedup (exact match)
    seen_urls = set()
    url_deduped = []
    for item in items:
        if item.url:
            normalized_url = item.url.rstrip("/").lower()
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
        url_deduped.append(item)

    before_url = len(items)
    after_url = len(url_deduped)

    # Stage 2: Headline similarity dedup
    # O(n²) but n is ~200-500, so ~0.1s
    keep = [True] * len(url_deduped)
    SIMILARITY_THRESHOLD = 0.75

    for i in range(len(url_deduped)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(url_deduped)):
            if not keep[j]:
                continue
            sim = _headline_similarity(
                url_deduped[i].headline, url_deduped[j].headline
            )
            if sim >= SIMILARITY_THRESHOLD:
                # Keep the one from the higher-ranked source
                rank_i = SOURCE_RANK.get(url_deduped[i].source_name or "", 5)
                rank_j = SOURCE_RANK.get(url_deduped[j].source_name or "", 5)
                if rank_j > rank_i:
                    keep[i] = False
                    break  # i is dead, stop comparing
                else:
                    keep[j] = False

    result = [item for item, k in zip(url_deduped, keep) if k]
    after_headline = len(result)

    print(f"[Dedup] {before_url} → {after_url} (URL) → {after_headline} (headline)")
    return result
