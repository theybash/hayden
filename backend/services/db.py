import sqlite3
import json
import os
from datetime import datetime
from typing import List
from models.news import NewsItem

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hayden.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_items (
            id TEXT PRIMARY KEY,
            headline TEXT NOT NULL,
            source TEXT NOT NULL,
            source_name TEXT,
            url TEXT,
            timestamp TEXT NOT NULL,
            impact_score INTEGER DEFAULT 0,
            relevance_score INTEGER DEFAULT 100,
            category TEXT,
            symbol TEXT,
            summary TEXT,
            description TEXT,
            cluster_id TEXT,
            source_count INTEGER DEFAULT 1,
            cluster_headlines TEXT,
            cluster_urls TEXT,
            cluster_sources TEXT,
            raw_data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_timestamp ON news_items(timestamp DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_impact ON news_items(impact_score DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_category ON news_items(category)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_symbol ON news_items(symbol)
    """)

    # Chat tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New Chat',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            context_sources TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at)
    """)

    # Annual reports index tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            company_name TEXT,
            from_yr TEXT,
            to_yr TEXT,
            file_url TEXT,
            page_count INTEGER,
            pdf_path TEXT,
            indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_indexed_reports_conv ON indexed_reports(conversation_id)
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Initialized at {DB_PATH}")


def save_items(items: List[NewsItem]):
    """Upsert news items into the database."""
    if not items:
        return

    conn = _get_conn()
    rows = []
    for item in items:
        rows.append((
            item.id,
            item.headline,
            item.source,
            item.source_name,
            item.url,
            item.timestamp.isoformat(),
            item.impact_score,
            item.relevance_score,
            item.category,
            item.symbol,
            item.summary,
            item.description,
            item.cluster_id,
            item.source_count,
            json.dumps(item.cluster_headlines) if item.cluster_headlines else None,
            json.dumps(item.cluster_urls) if item.cluster_urls else None,
            json.dumps(item.cluster_sources) if item.cluster_sources else None,
            json.dumps(item.raw_data) if item.raw_data else None,
        ))

    conn.executemany("""
        INSERT INTO news_items (
            id, headline, source, source_name, url, timestamp,
            impact_score, relevance_score, category, symbol, summary,
            description, cluster_id, source_count,
            cluster_headlines, cluster_urls, cluster_sources, raw_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            impact_score = excluded.impact_score,
            relevance_score = excluded.relevance_score,
            category = excluded.category,
            summary = excluded.summary,
            cluster_id = excluded.cluster_id,
            source_count = excluded.source_count,
            cluster_headlines = excluded.cluster_headlines,
            cluster_urls = excluded.cluster_urls,
            cluster_sources = excluded.cluster_sources
    """, rows)
    conn.commit()
    conn.close()
    print(f"[DB] Saved {len(rows)} items")


def _row_to_item(row: tuple) -> NewsItem:
    """Convert a database row to a NewsItem."""
    return NewsItem(
        id=row[0],
        headline=row[1],
        source=row[2],
        source_name=row[3],
        url=row[4],
        timestamp=datetime.fromisoformat(row[5]),
        impact_score=row[6],
        relevance_score=row[7],
        category=row[8],
        symbol=row[9],
        summary=row[10],
        description=row[11],
        cluster_id=row[12],
        source_count=row[13],
        cluster_headlines=json.loads(row[14]) if row[14] else None,
        cluster_urls=json.loads(row[15]) if row[15] else None,
        cluster_sources=json.loads(row[16]) if row[16] else None,
        raw_data=json.loads(row[17]) if row[17] else None,
    )


def load_recent(hours: int = 72) -> List[NewsItem]:
    """Load recent news items from the database."""
    conn = _get_conn()
    cutoff = datetime.utcnow().isoformat()
    # Load everything from last N hours, sorted by timestamp desc
    cursor = conn.execute("""
        SELECT id, headline, source, source_name, url, timestamp,
               impact_score, relevance_score, category, symbol, summary,
               description, cluster_id, source_count,
               cluster_headlines, cluster_urls, cluster_sources, raw_data
        FROM news_items
        WHERE timestamp >= datetime(?, '-' || ? || ' hours')
        ORDER BY timestamp DESC
    """, (cutoff, hours))

    items = [_row_to_item(row) for row in cursor.fetchall()]
    conn.close()
    print(f"[DB] Loaded {len(items)} items from last {hours}h")
    return items


# ── Chat helpers ──

def create_conversation(conv_id: str, title: str = "New Chat") -> dict:
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conv_id, title, now, now),
    )
    conn.commit()
    conn.close()
    return {"id": conv_id, "title": title, "created_at": now, "updated_at": now}


def list_conversations() -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]} for r in rows]


def update_conversation_title(conv_id: str, title: str):
    conn = _get_conn()
    conn.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                 (title, datetime.utcnow().isoformat(), conv_id))
    conn.commit()
    conn.close()


def delete_conversation(conv_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()


def add_message(conv_id: str, role: str, content: str, context_sources: str = None) -> dict:
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, context_sources, created_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, role, content, context_sources, now),
    )
    msg_id = cursor.lastrowid
    conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))
    conn.commit()
    conn.close()
    return {"id": msg_id, "role": role, "content": content, "context_sources": context_sources, "created_at": now}


def get_messages(conv_id: str) -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, role, content, context_sources, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "role": r[1], "content": r[2], "context_sources": r[3], "created_at": r[4]} for r in rows]


def get_news_by_symbols(symbols: list, hours: int = 168) -> list:
    """Get news items for specific symbols from the last N hours."""
    if not symbols:
        return []
    conn = _get_conn()
    cutoff = datetime.utcnow().isoformat()
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(f"""
        SELECT headline, source_name, url, timestamp, impact_score, category, symbol, description
        FROM news_items
        WHERE symbol IN ({placeholders})
        AND timestamp >= datetime(?, '-' || ? || ' hours')
        ORDER BY timestamp DESC
        LIMIT 30
    """, (*[s.upper() for s in symbols], cutoff, hours)).fetchall()
    conn.close()
    return [{"headline": r[0], "source_name": r[1], "url": r[2], "timestamp": r[3],
             "impact_score": r[4], "category": r[5], "symbol": r[6], "description": r[7]} for r in rows]


def save_summary(item_id: str, summary: str):
    """Persist a generated summary."""
    conn = _get_conn()
    conn.execute("UPDATE news_items SET summary = ? WHERE id = ?", (summary, item_id))
    conn.commit()
    conn.close()


# ── Indexed Reports helpers ──

def save_indexed_report(conv_id: str, symbol: str, company_name: str,
                        from_yr: str, to_yr: str, file_url: str,
                        page_count: int, pdf_path: str = None):
    conn = _get_conn()
    conn.execute("""
        INSERT INTO indexed_reports (conversation_id, symbol, company_name, from_yr, to_yr, file_url, page_count, pdf_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (conv_id, symbol, company_name, from_yr, to_yr, file_url, page_count, pdf_path))
    conn.commit()
    conn.close()


def get_indexed_reports(conv_id: str) -> list:
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, conversation_id, symbol, company_name, from_yr, to_yr, file_url, page_count, pdf_path, indexed_at
        FROM indexed_reports WHERE conversation_id = ?
    """, (conv_id,)).fetchall()
    conn.close()
    return [
        {"id": r[0], "conversation_id": r[1], "symbol": r[2], "company_name": r[3],
         "from_yr": r[4], "to_yr": r[5], "file_url": r[6], "page_count": r[7],
         "pdf_path": r[8], "indexed_at": r[9]}
        for r in rows
    ]


def delete_indexed_reports(conv_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM indexed_reports WHERE conversation_id = ?", (conv_id,))
    conn.commit()
    conn.close()
