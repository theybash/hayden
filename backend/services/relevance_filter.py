import json
from typing import List
from openai import OpenAI
from models.news import NewsItem
import config

# Items from these sources are always relevant (skip LLM check)
ALWAYS_RELEVANT_SOURCES = {"nse", "web"}
ALWAYS_RELEVANT_CATEGORIES = {"regulatory"}

RELEVANCE_THRESHOLD = 25  # Kill anything below this
BATCH_SIZE = 50  # Max headlines per LLM call


def _score_batch(client: OpenAI, batch: List[NewsItem]) -> None:
    """Score a single batch of items with LLM."""
    headlines = []
    for i, item in enumerate(batch):
        cat_hint = f" [{item.category}]" if item.category else ""
        src_hint = f" ({item.source_name})" if item.source_name else ""
        headlines.append(f"{i+1}. {item.headline[:120]}{src_hint}{cat_hint}")

    headlines_text = "\n".join(headlines)

    try:
        response = client.chat.completions.create(
            model=config.SCORING_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are filtering news for an Indian stock market researcher. "
                        "For each item, provide TWO things:\n"
                        "1. relevance_score (0-100): How relevant is this to Indian equity markets?\n"
                        "   90-100 = directly about Indian stocks, NSE/BSE companies, Indian economy\n"
                        "   70-90 = global event that clearly affects Indian markets (Fed rate, oil prices, China trade)\n"
                        "   60-80 = geopolitical conflict, military action, sanctions, or events near oil routes/trade hubs "
                        "(Middle East, Strait of Hormuz, Red Sea, South China Sea) — these ALWAYS matter for Indian markets via oil, FII flows, and risk sentiment\n"
                        "   30-50 = global financial news with possible indirect impact\n"
                        "   10-25 = general news, weak market connection\n"
                        "   0-10 = sports, entertainment, celebrity, weather, lifestyle, local US politics\n"
                        "2. category: one of 'indian_markets', 'global', 'regulatory', 'macro', 'crypto'\n\n"
                        "Be AGGRESSIVE about filtering entertainment/lifestyle. But NEVER filter out geopolitical conflict — "
                        "wars, strikes, sanctions, military escalation always move markets.\n"
                        "Return a JSON array of objects: [{\"r\": score, \"c\": \"category\"}, ...]\n"
                        "One object per item, same order. Return ONLY valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Score these {len(batch)} items:\n\n{headlines_text}",
                },
            ],
        )

        raw_text = (response.choices[0].message.content or "").strip()

        # Strip markdown fences
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_text = "\n".join(lines)

        scores = json.loads(raw_text)

        if isinstance(scores, list) and len(scores) == len(batch):
            for item, score_obj in zip(batch, scores):
                if isinstance(score_obj, dict):
                    item.relevance_score = max(0, min(100, int(score_obj.get("r", 50))))
                    cat = score_obj.get("c", item.category or "global")
                    if cat in ("indian_markets", "global", "regulatory", "macro", "crypto"):
                        item.category = cat
                elif isinstance(score_obj, (int, float)):
                    item.relevance_score = max(0, min(100, int(score_obj)))
        else:
            print(f"[Relevance] Batch score mismatch: got {len(scores)}, expected {len(batch)}")
            for item in batch:
                item.relevance_score = 30

    except Exception as e:
        print(f"[Relevance] Batch scoring failed: {e}")
        for item in batch:
            item.relevance_score = 30


def filter_and_categorize(items: List[NewsItem]) -> List[NewsItem]:
    """
    Batch-score items for relevance to Indian equity markets.
    Also assigns categories if not already set.
    Returns only items scoring above threshold.
    """
    if not items:
        return items

    # Split: items that are auto-relevant vs need scoring
    auto_relevant = []
    needs_scoring = []

    for item in items:
        if item.source in ALWAYS_RELEVANT_SOURCES:
            if not item.category:
                item.category = "indian_markets"
            item.relevance_score = 80
            auto_relevant.append(item)
        elif item.category in ALWAYS_RELEVANT_CATEGORIES:
            item.relevance_score = 70
            auto_relevant.append(item)
        else:
            needs_scoring.append(item)

    if not needs_scoring:
        print(f"[Relevance] All {len(auto_relevant)} items auto-relevant, 0 to score")
        return auto_relevant

    if not config.OPENAI_API_KEY:
        print("[Relevance] No API key, passing all items through")
        for item in needs_scoring:
            item.relevance_score = 50
        return auto_relevant + needs_scoring

    # Score in batches
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    total_batches = (len(needs_scoring) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"[Relevance] Scoring {len(needs_scoring)} items in {total_batches} batches...")

    for i in range(0, len(needs_scoring), BATCH_SIZE):
        batch = needs_scoring[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        print(f"[Relevance] Batch {batch_num}/{total_batches} ({len(batch)} items)")
        _score_batch(client, batch)

    # Filter by threshold
    all_items = auto_relevant + needs_scoring
    before = len(all_items)
    filtered = [item for item in all_items if item.relevance_score >= RELEVANCE_THRESHOLD]
    after = len(filtered)

    print(f"[Relevance] {before} → {after} (killed {before - after} irrelevant items)")
    return filtered
