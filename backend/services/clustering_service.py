import re
import hashlib
from collections import defaultdict
from typing import List, Set
from models.news import NewsItem

# Common words to ignore when computing keyword overlap
STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "about", "between",
    "through", "after", "before", "during", "above", "below", "and", "but",
    "or", "nor", "not", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some", "such",
    "no", "only", "own", "same", "than", "too", "very", "just", "because",
    "if", "when", "while", "where", "how", "what", "which", "who", "whom",
    "this", "that", "these", "those", "it", "its", "he", "she", "they",
    "them", "his", "her", "their", "our", "your", "my", "up", "out",
    "new", "also", "says", "said", "report", "reports", "news", "today",
    "market", "markets", "stock", "stocks", "share", "shares", "india",
    "indian", "rs", "crore", "lakh", "per", "cent", "percent",
}


def _extract_keywords(headline: str) -> Set[str]:
    """Extract meaningful keywords from a headline."""
    words = re.findall(r"[a-zA-Z]{3,}", headline.lower())
    return {w for w in words if w not in STOP_WORDS}


def _keyword_overlap(kw_a: Set[str], kw_b: Set[str]) -> float:
    """Jaccard-like overlap between keyword sets."""
    if not kw_a or not kw_b:
        return 0.0
    intersection = kw_a & kw_b
    union = kw_a | kw_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


CLUSTER_THRESHOLD = 0.40  # 40% keyword overlap = same story


def cluster_items(items: List[NewsItem]) -> List[NewsItem]:
    """
    Group items covering the same story.
    Returns one representative item per cluster with source_count, cluster_headlines, etc.
    Non-clustered items pass through unchanged.
    """
    if not items:
        return items

    # Extract keywords for each item
    keywords = [_extract_keywords(item.headline) for item in items]

    # Build clusters using union-find approach
    n = len(items)
    cluster_map = list(range(n))  # each item starts as its own cluster

    def find(x):
        while cluster_map[x] != x:
            cluster_map[x] = cluster_map[cluster_map[x]]
            x = cluster_map[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            cluster_map[ry] = rx

    # Compare all pairs
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            overlap = _keyword_overlap(keywords[i], keywords[j])
            if overlap >= CLUSTER_THRESHOLD:
                union(i, j)

    # Group by cluster root
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    # Build result: pick best item per cluster, attach metadata
    result = []
    cluster_count = 0

    for root_idx, member_indices in groups.items():
        members = [items[i] for i in member_indices]

        if len(members) == 1:
            # Not clustered
            result.append(members[0])
            continue

        cluster_count += 1

        # Pick representative: highest impact score, then highest relevance, then highest source rank
        members.sort(key=lambda x: (x.impact_score, x.relevance_score), reverse=True)
        lead = members[0]
        others = members[1:]

        # Generate stable cluster ID
        all_headlines = sorted(m.headline for m in members)
        cluster_id = hashlib.md5("||".join(all_headlines).encode()).hexdigest()[:10]

        lead.cluster_id = cluster_id
        lead.source_count = len(members)
        lead.cluster_headlines = [m.headline for m in others][:10]
        lead.cluster_urls = [m.url for m in others if m.url][:10]
        lead.cluster_sources = [m.source_name or m.source for m in others][:10]

        result.append(lead)

    print(f"[Cluster] {len(items)} items → {len(result)} ({cluster_count} clusters found)")
    return result
