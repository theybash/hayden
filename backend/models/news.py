from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class NewsItem(BaseModel):
    id: str
    headline: str
    source: str  # "nse" | "web" | "rss"
    source_name: Optional[str] = None  # e.g. "MoneyControl", "Reuters", "CNN"
    url: Optional[str] = None
    timestamp: datetime
    impact_score: int = 0  # 0-100
    relevance_score: int = 100  # 0-100, how relevant to Indian markets
    category: Optional[str] = None  # "indian_markets" | "global" | "regulatory" | "macro" | "crypto"
    symbol: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None  # RSS description text
    cluster_id: Optional[str] = None  # groups related stories
    source_count: int = 1  # how many sources cover this story (for clusters)
    cluster_headlines: Optional[List[str]] = None  # other headlines in cluster
    cluster_urls: Optional[List[str]] = None  # other source URLs in cluster
    cluster_sources: Optional[List[str]] = None  # other source names in cluster
    raw_data: Optional[dict] = None


class NewsResponse(BaseModel):
    items: List[NewsItem]
    total: int
    stats: Optional[dict] = None  # pipeline stats


class SummaryResponse(BaseModel):
    summary: str


class NewsletterSource(BaseModel):
    index: int  # [1], [2], etc.
    headline: str
    url: Optional[str] = None
    source_name: Optional[str] = None


class NewsletterResponse(BaseModel):
    text: str
    word_count: int
    generated_at: datetime
    sources_count: int
    sources: List[NewsletterSource] = []
