import httpx
import time
import hashlib
from datetime import datetime
from typing import List, Optional
from models.news import NewsItem
import config

_client: Optional[httpx.Client] = None
_cookies_valid = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers=HEADERS,
            timeout=config.NSE_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    return _client


def bootstrap_cookies() -> None:
    """Visit NSE homepage to acquire Akamai cookies."""
    global _cookies_valid
    client = _get_client()
    try:
        client.get("https://www.nseindia.com")
        time.sleep(1.0)
        _cookies_valid = True
    except Exception:
        _cookies_valid = False


def _make_id(item: dict) -> str:
    raw = (
        f'nse_{item.get("symbol", "").strip()}_'
        f'{item.get("exchdisstime", "").strip()}_'
        f'{item.get("attchmntFile", "").strip()}'
    ).replace(" ", "")
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def fetch_announcements() -> List[NewsItem]:
    """Fetch corporate announcements from NSE."""
    global _cookies_valid

    if not _cookies_valid:
        bootstrap_cookies()

    client = _get_client()
    url = "https://www.nseindia.com/api/corporate-announcements?index=equities"

    try:
        resp = client.get(url)
        if resp.status_code in (401, 403):
            # Session expired, re-bootstrap
            bootstrap_cookies()
            resp = client.get(url)
        resp.raise_for_status()
    except Exception as e:
        print(f"[NSE] Failed to fetch announcements: {e}")
        return []

    data = resp.json()
    items = []

    for d in data:
        symbol = d.get("symbol", "").strip()
        desc = d.get("desc", "")
        subject = d.get("attchmntText", "") or d.get("subject", "") or desc
        headline = subject[:200] if subject else desc[:200]

        # Parse timestamp
        ts_str = d.get("exchdisstime", "")
        try:
            ts = datetime.strptime(ts_str, "%d-%b-%Y %H:%M:%S")
        except (ValueError, TypeError):
            ts = datetime.now()

        # Build URL to attachment if available
        att_file = d.get("attchmntFile", "")
        if att_file:
            if att_file.startswith("http"):
                item_url = att_file
            else:
                item_url = f"https://www.nseindia.com{att_file}"
        else:
            item_url = None

        items.append(NewsItem(
            id=_make_id(d),
            headline=headline,
            source="nse",
            url=item_url,
            timestamp=ts,
            impact_score=0,  # scored later
            symbol=symbol if symbol else None,
            raw_data=d,
        ))

    return items
