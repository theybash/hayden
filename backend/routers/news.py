from fastapi import APIRouter, Query
from typing import List, Optional
from models.news import NewsItem, NewsResponse, SummaryResponse, NewsletterResponse, NewsletterSource
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
from services import (
    nse_service,
    rss_service,
    dedup_service,
    relevance_filter,
    impact_scorer,
    clustering_service,
)
from services import db
from openai import OpenAI
import config

router = APIRouter(prefix="/api/news", tags=["news"])

# In-memory news store
_news_store: List[NewsItem] = []
_pipeline_stats: dict = {}


def _do_refresh_sync() -> List[NewsItem]:
    """
    Full news pipeline (sync — runs in thread):
    1. Fetch from NSE + Web + RSS
    2. Deduplicate (URL + headline similarity)
    3. Relevance filter + categorize (LLM)
    4. Impact score (LLM)
    5. Cluster related stories
    6. Sort and store
    """
    global _news_store, _pipeline_stats

    stats = {}

    # Stage 1: Fetch from all sources (RSS + NSE — web search disabled, RSS covers it better)
    nse_items = nse_service.fetch_announcements()
    rss_items = rss_service.fetch_all_feeds()

    all_items = nse_items + rss_items
    stats["raw"] = len(all_items)
    stats["nse"] = len(nse_items)
    stats["web"] = 0
    stats["rss"] = len(rss_items)
    print(f"[Pipeline] Stage 1 — Fetched {len(all_items)} raw (NSE:{len(nse_items)} RSS:{len(rss_items)})")

    # Stage 2: Deduplicate
    all_items = dedup_service.deduplicate(all_items)
    stats["after_dedup"] = len(all_items)

    # Stage 3: Relevance filter + categorize
    all_items = relevance_filter.filter_and_categorize(all_items)
    stats["after_relevance"] = len(all_items)

    # Stage 4: Impact score (only relevant items — saves tokens)
    all_items = impact_scorer.score_items(all_items)
    stats["after_scoring"] = len(all_items)

    # Stage 5: Cluster related stories
    all_items = clustering_service.cluster_items(all_items)
    stats["after_clustering"] = len(all_items)

    # Normalize timestamps and sort
    for item in all_items:
        if item.timestamp.tzinfo is not None:
            item.timestamp = item.timestamp.replace(tzinfo=None)
    all_items.sort(key=lambda x: x.timestamp, reverse=True)

    # Merge with existing DB items (keep old items not in this fetch)
    db.save_items(all_items)

    # Reload from DB to include historical items
    _news_store = db.load_recent(hours=72)
    _pipeline_stats = stats

    print(
        f"[Pipeline] Done — {stats['raw']} raw → {stats['after_dedup']} dedup → "
        f"{stats['after_relevance']} relevant → {stats['after_clustering']} final"
        f" | DB total: {len(_news_store)}"
    )

    return _news_store


async def do_refresh() -> List[NewsItem]:
    """Async wrapper — runs the blocking pipeline in a thread so it doesn't block the event loop."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_refresh_sync)


def load_from_db():
    """Load persisted news on startup (no fetch needed)."""
    global _news_store
    _news_store = db.load_recent(hours=72)
    print(f"[Startup] Loaded {len(_news_store)} items from DB")


@router.get("", response_model=NewsResponse)
async def get_news(
    min_impact: int = Query(0, ge=0, le=100),
    category: Optional[str] = Query(None, description="Filter by category: indian_markets, global, regulatory, macro, crypto"),
    source: Optional[str] = Query(None, description="Filter by source type: nse, web, rss"),
):
    """Get all news items with optional filters."""
    filtered = _news_store

    if min_impact > 0:
        filtered = [item for item in filtered if item.impact_score >= min_impact]
    if category:
        filtered = [item for item in filtered if item.category == category]
    if source:
        filtered = [item for item in filtered if item.source == source]

    return NewsResponse(items=filtered, total=len(filtered), stats=_pipeline_stats)


@router.get("/refresh", response_model=NewsResponse)
async def refresh_news():
    """Trigger a fresh fetch through the full pipeline."""
    items = await do_refresh()
    return NewsResponse(items=items, total=len(items), stats=_pipeline_stats)


@router.get("/newsletter", response_model=NewsletterResponse)
async def generate_newsletter(
    mode: str = Query("rundown", description="Briefing mode: glance, rundown, or full"),
    hours: int = Query(12, ge=1, le=72, description="How many hours back to cover"),
):
    """Generate a timeline-style newsletter briefing from recent news."""
    if not config.OPENAI_API_KEY:
        return NewsletterResponse(
            text="OpenAI API key not configured.",
            word_count=0,
            generated_at=datetime.utcnow(),
            sources_count=0,
        )

    # Filter items within the time window
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    recent_items = [
        item for item in _news_store
        if item.timestamp.replace(tzinfo=None) >= cutoff
    ]

    # Sort by timestamp (chronological for timeline), take top 50
    recent_items.sort(key=lambda x: x.timestamp)
    recent_items = recent_items[:50]

    if not recent_items:
        return NewsletterResponse(
            text="No news items in this time window. Try expanding the time range or refreshing the feed.",
            word_count=0,
            generated_at=datetime.utcnow(),
            sources_count=0,
        )

    # Build chronological headlines block with IST timestamps
    def to_ist(ts: datetime) -> str:
        utc_ts = ts.replace(tzinfo=timezone.utc)
        return utc_ts.astimezone(IST).strftime('%I:%M %p IST')

    # Build numbered source list and headlines block
    sources = []
    headlines_lines = []
    for i, item in enumerate(recent_items, 1):
        sources.append(NewsletterSource(
            index=i,
            headline=item.headline,
            url=item.url,
            source_name=item.source_name or item.source,
        ))
        line = f"[{i}] [{to_ist(item.timestamp)}] (impact:{item.impact_score}) {item.headline}"
        if item.description:
            line += f": {item.description[:150]}"
        headlines_lines.append(line)

    headlines_block = "\n".join(headlines_lines)

    mode_config = {
        "glance": {"label": "Quick Glance", "words": 100},
        "rundown": {"label": "The Rundown", "words": 250},
        "full": {"label": "Full Brief", "words": 500},
    }
    cfg = mode_config.get(mode, mode_config["rundown"])

    time_label = f"last {hours} hour{'s' if hours != 1 else ''}"

    prompt = f"""You are a sharp financial newsletter writer. Write a "{cfg['label']}" briefing (~{cfg['words']} words) as a TIMELINE of what happened in the {time_label}.

Rules:
- Structure it chronologically — walk the reader through the {time_label} like a timeline, earliest to latest
- Use time markers (e.g. "Early morning:", "Mid-day:", "Afternoon:") to anchor the narrative
- Write in plain English a smart non-expert can follow
- When you use financial jargon or acronyms, explain them in [brackets] the first time (e.g. "FII [Foreign Institutional Investor]")
- Connect stories where they relate — show cause and effect across the timeline
- CITE sources using [1], [2], [3] etc. inline whenever you reference a story. Every claim should have at least one citation
- End with a short "What to watch next" section (2-3 bullet points)
- No greetings or sign-offs, jump straight into the timeline
- Keep it to ~{cfg['words']} words

News from the {time_label} (numbered for citation, chronological):
{headlines_block}"""

    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=config.NEWSLETTER_MODEL,
            messages=[
                {"role": "system", "content": "You write concise, timeline-style financial news briefings with inline citations [1][2] etc. that walk readers through what happened chronologically."},
                {"role": "user", "content": prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        return NewsletterResponse(
            text=text,
            word_count=len(text.split()),
            generated_at=datetime.utcnow(),
            sources_count=len(recent_items),
            sources=sources,
        )
    except Exception as e:
        return NewsletterResponse(
            text=f"Failed to generate newsletter: {str(e)}",
            word_count=0,
            generated_at=datetime.utcnow(),
            sources_count=0,
        )


@router.get("/{item_id}/summary", response_model=SummaryResponse)
async def get_summary(item_id: str):
    """Generate an AI summary for a specific news item."""
    item = next((i for i in _news_store if i.id == item_id), None)
    if not item:
        return SummaryResponse(summary="News item not found.")

    if item.summary:
        return SummaryResponse(summary=item.summary)

    if not config.OPENAI_API_KEY:
        return SummaryResponse(summary="OpenAI API key not configured.")

    client = OpenAI(api_key=config.OPENAI_API_KEY)

    context_parts = [f"Headline: {item.headline}"]
    if item.symbol:
        context_parts.append(f"Stock: {item.symbol}")
    if item.description:
        context_parts.append(f"Description: {item.description}")
    if item.source == "nse" and item.raw_data:
        desc = item.raw_data.get("desc", "")
        att_text = item.raw_data.get("attchmntText", "")
        if desc:
            context_parts.append(f"Filing: {desc}")
        if att_text:
            context_parts.append(f"Details: {att_text}")
    if item.source_name:
        context_parts.append(f"Source: {item.source_name}")
    if item.url:
        context_parts.append(f"URL: {item.url}")

    context = "\n".join(context_parts)

    try:
        response = client.chat.completions.create(
            model=config.SUMMARY_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise financial news analyst. Summarize the news item "
                        "in 2-3 sentences. Focus on: what happened, which company/sector is "
                        "affected, and the potential market impact. Be factual and specific."
                    ),
                },
                {"role": "user", "content": context},
            ],
        )
        summary = (response.choices[0].message.content or "").strip()
        item.summary = summary
        db.save_summary(item.id, summary)
        return SummaryResponse(summary=summary)
    except Exception as e:
        return SummaryResponse(summary=f"Failed to generate summary: {str(e)}")
