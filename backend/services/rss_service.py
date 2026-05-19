import hashlib
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Dict

import httpx

from models.news import NewsItem

FEEDS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "feeds.json")

# Map feed categories to our category labels
CATEGORY_MAP = {
    "indian_financial": "indian_markets",
    "indian_regulatory": "regulatory",
    "global_financial": "global",
    "global_general": "global",
    "central_banks": "macro",
    "crypto": "crypto",
}


def _load_feeds() -> Dict[str, list]:
    with open(FEEDS_PATH, "r") as f:
        return json.load(f)


def _parse_date(date_str: str) -> datetime:
    """Try multiple date formats commonly found in RSS feeds."""
    if not date_str:
        return datetime.now()

    # Try RFC 2822 (most RSS feeds)
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except Exception:
        pass

    # Try ISO format
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except Exception:
        pass

    # Common RSS date formats
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d %b %Y %H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue

    return datetime.now()


def _make_id(url: str, title: str, source_name: str) -> str:
    raw = f"rss_{url}_{title}_{source_name}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _parse_feed_xml(xml_text: str, feed_name: str, category: str) -> List[NewsItem]:
    """Parse RSS/Atom XML into NewsItem objects."""
    items = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        print(f"[RSS] Failed to parse XML from {feed_name}")
        return []

    # Handle RSS 2.0
    rss_items = root.findall(".//item")

    # Handle Atom feeds
    if not rss_items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        rss_items = root.findall(".//atom:entry", ns)
        if not rss_items:
            # Try without namespace
            rss_items = root.findall(".//entry")

    for item_el in rss_items:
        # Extract title
        title_el = item_el.find("title")
        if title_el is None:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            title_el = item_el.find("atom:title", ns)
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue

        # Extract link
        link_el = item_el.find("link")
        if link_el is not None:
            link = link_el.text or link_el.get("href", "") or ""
        else:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            link_el = item_el.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
        link = link.strip()

        # Extract date
        date_str = ""
        for date_tag in ["pubDate", "published", "updated", "dc:date"]:
            date_el = item_el.find(date_tag)
            if date_el is not None and date_el.text:
                date_str = date_el.text.strip()
                break
        # Try with dc namespace
        if not date_str:
            dc_ns = {"dc": "http://purl.org/dc/elements/1.1/"}
            date_el = item_el.find("dc:date", dc_ns)
            if date_el is not None and date_el.text:
                date_str = date_el.text.strip()

        ts = _parse_date(date_str)

        # Extract description
        desc_el = item_el.find("description")
        if desc_el is None:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            desc_el = item_el.find("atom:summary", ns)
            if desc_el is None:
                desc_el = item_el.find("summary")
        description = ""
        if desc_el is not None and desc_el.text:
            # Strip HTML tags from description
            import re
            description = re.sub(r"<[^>]+>", "", desc_el.text).strip()[:500]

        items.append(NewsItem(
            id=_make_id(link, title, feed_name),
            headline=title[:300],
            source="rss",
            source_name=feed_name,
            url=link if link else None,
            timestamp=ts,
            category=category,
            description=description if description else None,
        ))

    return items


def fetch_all_feeds() -> List[NewsItem]:
    """Fetch all RSS feeds in parallel and return NewsItem objects."""
    feeds = _load_feeds()
    all_items: List[NewsItem] = []

    # Build flat list of (url, name, category)
    feed_list = []
    for cat_key, feed_entries in feeds.items():
        category = CATEGORY_MAP.get(cat_key, "global")
        for entry in feed_entries:
            feed_list.append((entry["url"], entry["name"], category))

    print(f"[RSS] Fetching {len(feed_list)} feeds...")

    # Fetch all feeds with httpx (sync, with timeout)
    results = {}
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for url, name, category in feed_list:
            try:
                resp = client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Hayden/2.0; +https://github.com/hayden)",
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                })
                if resp.status_code == 200:
                    results[(url, name, category)] = resp.text
                else:
                    print(f"[RSS] {name}: HTTP {resp.status_code}")
            except Exception as e:
                print(f"[RSS] {name}: Failed - {type(e).__name__}: {e}")

    print(f"[RSS] Got responses from {len(results)}/{len(feed_list)} feeds")

    # Parse all feeds
    for (url, name, category), xml_text in results.items():
        items = _parse_feed_xml(xml_text, name, category)
        all_items.extend(items)

    print(f"[RSS] Parsed {len(all_items)} total items from RSS feeds")
    return all_items
