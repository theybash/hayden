"""Markdown notes per chat — agents save research findings here."""

import os
import json
import glob
from datetime import datetime
import config


def _notes_dir(conv_id: str) -> str:
    d = os.path.join(config.CHAT_DATA_DIR, conv_id, "notes")
    os.makedirs(d, exist_ok=True)
    return d


def create_note(conv_id: str, title: str, content: str) -> dict:
    """Save a markdown note. Returns filename + preview."""
    d = _notes_dir(conv_id)
    # Generate sequential filename
    existing = sorted(glob.glob(os.path.join(d, "note_*.md")))
    num = len(existing) + 1
    filename = f"note_{num:03d}.md"
    path = os.path.join(d, filename)

    full_content = f"# {title}\n\n{content}\n\n---\n_Created: {datetime.utcnow().isoformat()}_\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(full_content)

    return {"filename": filename, "title": title, "preview": content[:200]}


def list_notes(conv_id: str) -> list:
    """List all notes for a conversation."""
    d = _notes_dir(conv_id)
    notes = []
    for path in sorted(glob.glob(os.path.join(d, "note_*.md"))):
        filename = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # Extract title from first line
        title = text.split("\n")[0].lstrip("# ").strip() if text else filename
        notes.append({
            "filename": filename,
            "title": title,
            "preview": text[:300],
        })
    return notes


def read_note(conv_id: str, filename: str) -> str:
    """Read a note's full content."""
    path = os.path.join(_notes_dir(conv_id), filename)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def delete_note(conv_id: str, filename: str) -> bool:
    """Delete a note."""
    path = os.path.join(_notes_dir(conv_id), filename)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
