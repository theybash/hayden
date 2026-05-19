"""Core RAG engine for annual reports — fetch, download, index, search, vision."""

import os
import io
import re
import json
import time
import shutil
import zipfile
import tempfile
import base64
import threading
from typing import List, Optional

import unicodedata
import fitz  # PyMuPDF
import faiss
import numpy as np
from openai import OpenAI

import config
from services import db

# ── File locks per conversation (prevent concurrent indexing) ──
_conv_locks: dict[str, threading.Lock] = {}


def _get_lock(conv_id: str) -> threading.Lock:
    if conv_id not in _conv_locks:
        _conv_locks[conv_id] = threading.Lock()
    return _conv_locks[conv_id]


def _get_client() -> OpenAI:
    return OpenAI(api_key=config.OPENAI_API_KEY)


# ── Page Quality Scoring ──

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "and", "but", "or", "nor", "not", "so", "yet",
    "both", "either", "neither", "each", "every", "all", "any", "few",
    "more", "most", "other", "some", "such", "no", "only", "own", "same",
    "than", "too", "very", "just", "because", "if", "when", "where",
    "how", "what", "which", "who", "whom", "this", "that", "these",
    "those", "it", "its", "they", "them", "their", "we", "our", "he",
    "she", "his", "her", "i", "me", "my", "you", "your",
})

_FINANCIAL_PHRASES = [
    "profit and loss", "income statement", "balance sheet", "cash flow",
    "revenue from operations", "statement of profit", "financial statements",
    "standalone financial", "consolidated financial", "notes to financial",
    "auditor's report", "directors report", "management discussion",
    "shareholders equity", "earnings per share", "dividend",
    "segment reporting", "related party", "contingent liabilities",
    "capital employed", "return on equity", "debt equity",
    "operating profit", "net profit", "gross profit", "ebitda",
    "total income", "total expenses", "tax expense",
]


def _normalize_text(text: str) -> str:
    """Normalize Unicode ligatures and compatibility chars.
    ﬁ→fi, ﬂ→fl, ﬀ→ff, ﬃ→ffi, ﬄ→ffl, ﬅ→st, etc.
    Uses NFKD decomposition which breaks ligatures into base chars."""
    return unicodedata.normalize("NFKC", text)


def _score_page_quality(text: str) -> str:
    """Score page text quality: 'good', 'table', or 'bad'."""
    if len(text) < 50:
        return "bad"
    alpha_chars = sum(1 for c in text if c.isalpha())
    alpha_ratio = alpha_chars / len(text) if text else 0
    if alpha_ratio < 0.30:
        return "bad"
    # Detect table-heavy pages: high numeric density + pipe/tab chars
    digit_chars = sum(1 for c in text if c.isdigit())
    digit_ratio = digit_chars / len(text) if text else 0
    if digit_ratio > 0.15 and ("|" in text or "\t" in text or text.count("  ") > 10):
        return "table"
    return "good"


# ── Paths ──

def _chat_dir(conv_id: str) -> str:
    d = os.path.join(config.CHAT_DATA_DIR, conv_id)
    os.makedirs(d, exist_ok=True)
    return d


def _pdf_dir(conv_id: str) -> str:
    d = os.path.join(_chat_dir(conv_id), "pdfs")
    os.makedirs(d, exist_ok=True)
    return d


def _pdf_index_dir(conv_id: str) -> str:
    d = os.path.join(_chat_dir(conv_id), "pdf_indexes")
    os.makedirs(d, exist_ok=True)
    return d


def _faiss_path(conv_id: str) -> str:
    return os.path.join(_chat_dir(conv_id), "index.faiss")


def _meta_path(conv_id: str) -> str:
    return os.path.join(_chat_dir(conv_id), "index_meta.json")


def _scoped_dir(conv_id: str) -> str:
    """Per-report scoped indexes live here: one .faiss + _meta.json per source_file."""
    d = os.path.join(_chat_dir(conv_id), "scoped_indexes")
    os.makedirs(d, exist_ok=True)
    return d


def _scoped_faiss_path(conv_id: str, source_file: str) -> str:
    return os.path.join(_scoped_dir(conv_id), f"{source_file}.faiss")


def _scoped_meta_path(conv_id: str, source_file: str) -> str:
    return os.path.join(_scoped_dir(conv_id), f"{source_file}_meta.json")


# ── NSE API: fetch report list ──

def fetch_report_list(symbol: str) -> list:
    """Fetch annual report list from NSE for a symbol.
    Reuses nse_service cookie pattern."""
    import httpx
    from services.nse_service import _get_client as _get_nse_client, bootstrap_cookies, _cookies_valid, HEADERS

    if not _cookies_valid:
        bootstrap_cookies()

    client = _get_nse_client()
    url = f"https://www.nseindia.com/api/annual-reports?index=equities&symbol={symbol.upper()}"

    try:
        resp = client.get(url)
        if resp.status_code in (401, 403):
            bootstrap_cookies()
            resp = client.get(url)
        resp.raise_for_status()
    except Exception as e:
        print(f"[AnnualReport] Failed to fetch report list for {symbol}: {e}")
        return []

    data = resp.json()
    # NSE API wraps the list in {"data": [...]}
    if isinstance(data, dict):
        data = data.get("data", [])
    results = []
    for item in data:
        company_name = item.get("companyName", symbol.upper())
        from_yr = item.get("fromYr", "")
        to_yr = item.get("toYr", "")
        file_name = item.get("fileName", "")

        if file_name:
            if file_name.startswith("http"):
                file_url = file_name
            else:
                file_url = f"https://www.nseindia.com{file_name}"
        else:
            continue

        file_type = "zip" if file_url.lower().endswith(".zip") else "pdf"
        results.append({
            "company_name": company_name,
            "symbol": symbol.upper(),
            "from_yr": from_yr,
            "to_yr": to_yr,
            "file_url": file_url,
            "file_type": file_type,
        })

    return results


# ── Download & Index ──

def _download_pdf(file_url: str, file_type: str) -> Optional[str]:
    """Download PDF (or ZIP → extract largest PDF). Returns path to PDF on disk."""
    import httpx
    from services.nse_service import _get_client as _get_nse_client, bootstrap_cookies

    client = _get_nse_client()
    try:
        resp = client.get(file_url)
        if resp.status_code in (401, 403):
            bootstrap_cookies()
            resp = client.get(file_url)
        resp.raise_for_status()
    except Exception as e:
        print(f"[AnnualReport] Download failed: {e}")
        return None

    if file_type == "zip":
        try:
            z = zipfile.ZipFile(io.BytesIO(resp.content))
            # Find largest PDF in ZIP
            pdf_names = [n for n in z.namelist() if n.lower().endswith(".pdf")]
            if not pdf_names:
                print("[AnnualReport] No PDF found in ZIP")
                return None
            largest = max(pdf_names, key=lambda n: z.getinfo(n).file_size)
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(z.read(largest))
            tmp.close()
            return tmp.name
        except Exception as e:
            print(f"[AnnualReport] ZIP extraction failed: {e}")
            return None
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(resp.content)
        tmp.close()
        return tmp.name


def _extract_text_pages(pdf_path: str) -> list[dict]:
    """Extract text from every page. Normalizes Unicode ligatures (ﬁ→fi etc).
    Returns [{page_num, text}]."""
    doc = fitz.open(pdf_path)
    pages = []
    for i in range(len(doc)):
        text = _normalize_text(doc[i].get_text().strip())
        pages.append({"page_num": i + 1, "text": text})
    doc.close()
    return pages


def _render_page_as_image(pdf_path: str, page_num: int, dpi: int = 200) -> str:
    """Render a PDF page as a base64-encoded PNG image for vision API."""
    doc = fitz.open(pdf_path)
    page = doc[page_num - 1]  # 0-indexed
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    doc.close()
    return base64.b64encode(img_bytes).decode("utf-8")


def _detect_toc_pages(pages_text: list[dict], max_scan: int = 12) -> list[int]:
    """Heuristic: find pages that look like a table of contents / index.
    Handles multiple formats:
    - "Section Name ... 42" (dots + number at end)
    - "Section Name  42" (number at end of line)
    - "Section Name\\n42" (number on its own line, Indian AR format)"""
    toc_pages = []
    for p in pages_text[:max_scan]:
        text = p["text"]
        lines = text.split("\n")
        if len(lines) < 3:
            continue

        # Count lines that end with a number (typical TOC: "Section ... 42")
        num_ending_lines = sum(1 for line in lines if re.search(r'\d+\s*$', line.strip()) and len(line.strip()) > 10)
        # Count lines with dots/leaders
        dot_lines = sum(1 for line in lines if '...' in line or '●' in line)
        # Count standalone number lines (Indian AR format: page num on its own line)
        standalone_nums = sum(1 for line in lines if re.match(r'^\s*\d{1,3}\s*$', line.strip()))
        # Pages with "index" or "contents" in text
        has_toc_keyword = bool(re.search(r'\b(index|contents|table of contents)\b', text.lower()))

        # Score this page
        is_toc = False
        if has_toc_keyword and (num_ending_lines >= 5 or dot_lines >= 3):
            is_toc = True
        elif has_toc_keyword and standalone_nums >= 5:
            # Indian format: "Section Name\n02\nNext Section\n04"
            is_toc = True
        elif num_ending_lines >= 8:
            is_toc = True
        elif standalone_nums >= 8 and has_toc_keyword:
            is_toc = True

        if is_toc:
            toc_pages.append(p["page_num"])
    return toc_pages


def _extract_toc_with_vision(pdf_path: str, toc_page_nums: list[int], symbol: str,
                              fiscal_year: str, total_pages: int) -> dict:
    """Render TOC pages as images, send to vision model for structured extraction.
    Returns {toc_text, key_sections[{title, doc_page, pdf_page}], page_map}."""
    client = _get_client()

    # Render TOC pages as images
    images = []
    for pn in toc_page_nums[:3]:  # Max 3 TOC pages
        try:
            img_b64 = _render_page_as_image(pdf_path, pn, dpi=250)
            images.append({"page_num": pn, "b64": img_b64})
        except Exception as e:
            print(f"[AnnualReport] Failed to render TOC page {pn}: {e}")

    if not images:
        return {"toc_text": "", "key_sections": [], "page_map": {}}

    # Build vision message with all TOC page images
    content_parts = [
        {"type": "text", "text": f"""This is the Index/Table of Contents from an Indian annual report ({symbol}, FY {fiscal_year}, {total_pages} PDF pages).

Extract ALL sections with their PAGE NUMBERS as printed in the document. Return JSON:
{{
  "toc_text": "raw text you can read from the TOC",
  "sections": [
    {{"title": "Section Name", "doc_page": 205}},
    ...
  ]
}}

IMPORTANT:
- "doc_page" is the page number PRINTED in the document (what the TOC shows)
- These are NOT PDF page numbers — the PDF page number is different
- Extract EVERY section/subsection you can see
- Include page numbers exactly as printed"""},
    ]
    for img in images:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img['b64']}", "detail": "high"},
        })

    try:
        resp = client.chat.completions.create(
            model=config.SUB_AGENT_MODEL,
            messages=[{"role": "user", "content": content_parts}],
            response_format={"type": "json_object"},
            max_tokens=4000,
        )
        toc_data = json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"[AnnualReport] Vision TOC extraction failed: {e}")
        return {"toc_text": "", "key_sections": [], "page_map": {}}

    # Build doc_page → pdf_page mapping
    # Strategy: find the first TOC page's own printed page number to compute offset
    # The TOC page itself is at pdf_page = toc_page_nums[0], and we can find its
    # printed page number from the extracted sections or by checking if the TOC
    # page has a page number on it
    sections = toc_data.get("sections", [])
    page_map = _build_page_map(pdf_path, sections, total_pages)

    key_sections = []
    for s in sections:
        doc_page = s.get("doc_page", 0)
        pdf_page = page_map.get(str(doc_page), doc_page)  # fallback to same number
        key_sections.append({
            "title": s.get("title", ""),
            "doc_page": doc_page,
            "pdf_page": pdf_page,
            "page": pdf_page,  # backward compat — agent uses this
        })

    return {
        "toc_text": toc_data.get("toc_text", ""),
        "key_sections": key_sections,
        "page_map": page_map,
    }


def _build_page_map(pdf_path: str, sections: list, total_pages: int) -> dict:
    """Build doc_page → pdf_page mapping by finding the offset.
    Scans a few pages near the start to find printed page numbers and compute offset."""
    doc = fitz.open(pdf_path)

    # Strategy: check pages near the middle of the document where printed page numbers
    # are more likely to be unambiguous. Look for a page number pattern at the bottom.
    offsets = []
    # Sample pages: try pages 10-20 (pdf) which usually have clear printed numbers
    sample_range = range(min(10, total_pages), min(25, total_pages))
    for pdf_idx in sample_range:
        page = doc[pdf_idx]
        text = _normalize_text(page.get_text().strip())
        lines = text.split('\n')
        if not lines:
            continue
        # Check last few lines for a standalone number (printed page number)
        for line in reversed(lines[-5:]):
            line = line.strip()
            match = re.match(r'^(\d{1,4})$', line)
            if match:
                printed_num = int(match.group(1))
                pdf_page = pdf_idx + 1  # 1-indexed
                offset = printed_num - pdf_page
                offsets.append(offset)
                break

    doc.close()

    if not offsets:
        # No offset detected — assume 1:1 mapping
        return {str(s.get("doc_page", 0)): s.get("doc_page", 0) for s in sections}

    # Use the most common offset (mode)
    from collections import Counter
    offset = Counter(offsets).most_common(1)[0][0]

    # Build mapping: pdf_page = doc_page - offset
    page_map = {}
    for s in sections:
        doc_page = s.get("doc_page", 0)
        pdf_page = doc_page - offset
        # Clamp to valid range
        if 1 <= pdf_page <= total_pages:
            page_map[str(doc_page)] = pdf_page
        else:
            page_map[str(doc_page)] = doc_page  # fallback

    return page_map


def _extract_toc_fallback(pages_text: list[dict], symbol: str, fiscal_year: str) -> dict:
    """Fallback: LLM on text if no TOC pages detected or vision fails."""
    first_pages = pages_text[:8]
    combined = "\n\n".join(
        f"--- PAGE {p['page_num']} ---\n{p['text'][:3000]}"
        for p in first_pages
    )

    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=config.SUB_AGENT_MODEL,
            messages=[
                {"role": "system", "content": "You extract table of contents from annual report pages. Return valid JSON only."},
                {"role": "user", "content": f"""Extract the table of contents from this annual report ({symbol}, FY {fiscal_year}).

Identify major section titles with their page numbers. Return JSON:
{{
  "toc_text": "the raw table of contents text as found in the document",
  "key_sections": [
    {{"title": "Section Name", "page": 5}},
    ...
  ]
}}

If no clear TOC exists, infer sections from headings you can see.

Document pages:
{combined}"""},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"[AnnualReport] TOC fallback extraction failed: {e}")
        return {"toc_text": "", "key_sections": []}


def _batch_embed(texts: list[str], batch_size: int = 50) -> np.ndarray:
    """Embed texts in batches using OpenAI embeddings. Returns numpy array."""
    client = _get_client()
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # Truncate each text to ~8000 chars to stay within token limits
        batch = [t[:8000] if t else " " for t in batch]
        try:
            resp = client.embeddings.create(
                model=config.EMBEDDING_MODEL,
                input=batch,
            )
            for item in resp.data:
                all_embeddings.append(item.embedding)
        except Exception as e:
            print(f"[AnnualReport] Embedding batch failed: {e}")
            # Fill with zeros for failed batch
            dim = 1536  # text-embedding-3-small dimension
            for _ in batch:
                all_embeddings.append([0.0] * dim)

    return np.array(all_embeddings, dtype=np.float32)


def download_and_index(conv_id: str, symbol: str, from_yr: str, to_yr: str, file_url: str) -> dict:
    """Download PDF, extract text, build FAISS index, extract TOC. Keep PDF on disk.
    Returns {pages_indexed, key_sections, toc_preview, pdf_path}."""

    lock = _get_lock(conv_id)
    with lock:
        fiscal_year = f"{from_yr}-{to_yr}"
        source_file = f"{symbol.upper()}_{fiscal_year}"

        # Check if already indexed
        existing = db.get_indexed_reports(conv_id)
        for r in existing:
            if r["symbol"] == symbol.upper() and r["from_yr"] == from_yr and r["to_yr"] == to_yr:
                # Already indexed — return existing metadata
                toc_data = get_pdf_index_pages(conv_id, symbol, fiscal_year)
                return {
                    "pages_indexed": r["page_count"],
                    "key_sections": toc_data.get("key_sections", []),
                    "toc_preview": toc_data.get("toc_text", "")[:500],
                    "pdf_path": r.get("pdf_path", ""),
                    "already_existed": True,
                }

        # Download
        print(f"[AnnualReport] Downloading {source_file}...")
        file_type = "zip" if file_url.lower().endswith(".zip") else "pdf"
        tmp_path = _download_pdf(file_url, file_type)
        if not tmp_path:
            return {"error": "Download failed"}

        # Move PDF to permanent storage
        pdf_dest = os.path.join(_pdf_dir(conv_id), f"{source_file}.pdf")
        shutil.move(tmp_path, pdf_dest)
        print(f"[AnnualReport] PDF saved to {pdf_dest}")

        # Extract text
        print(f"[AnnualReport] Extracting text from {source_file}...")
        pages = _extract_text_pages(pdf_dest)
        total_pages = len(pages)

        # Detect scanned/image PDFs (warn if many pages have < 50 chars)
        sparse_pages = sum(1 for p in pages if len(p["text"]) < 50)
        if sparse_pages > total_pages * 0.5:
            print(f"[AnnualReport] WARNING: {sparse_pages}/{total_pages} pages have very little text. May be scanned PDF.")

        # Extract TOC — vision-based if we detect a TOC page, else fallback
        print(f"[AnnualReport] Detecting TOC pages...")
        toc_page_nums = _detect_toc_pages(pages)
        if toc_page_nums:
            print(f"[AnnualReport] Found TOC on pages {toc_page_nums}, using vision extraction...")
            toc_data = _extract_toc_with_vision(pdf_dest, toc_page_nums, symbol, fiscal_year, total_pages)
        else:
            print(f"[AnnualReport] No TOC pages detected, using text fallback...")
            toc_data = _extract_toc_fallback(pages, symbol, fiscal_year)
        toc_data["symbol"] = symbol.upper()
        toc_data["company_name"] = symbol.upper()
        toc_data["fiscal_year"] = fiscal_year
        toc_data["total_pages"] = total_pages
        toc_data["toc_pages"] = toc_page_nums or list(range(1, min(9, total_pages + 1)))

        # Save TOC
        toc_path = os.path.join(_pdf_index_dir(conv_id), f"{source_file}.json")
        with open(toc_path, "w", encoding="utf-8") as f:
            json.dump(toc_data, f, indent=2)

        # Embed all pages
        print(f"[AnnualReport] Embedding {total_pages} pages...")
        texts = [p["text"] for p in pages]
        embeddings = _batch_embed(texts)

        # Load or create FAISS index
        faiss_path = _faiss_path(conv_id)
        meta_path = _meta_path(conv_id)

        dim = embeddings.shape[1] if embeddings.shape[0] > 0 else 1536
        if os.path.exists(faiss_path):
            index = faiss.read_index(faiss_path)
            with open(meta_path, "r") as f:
                metadata = json.load(f)
        else:
            index = faiss.IndexFlatIP(dim)  # inner product (we'll normalize)
            metadata = []

        # Normalize for cosine similarity
        if embeddings.shape[0] > 0:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms[norms == 0] = 1
            embeddings = embeddings / norms

            # Add to index
            start_id = len(metadata)
            index.add(embeddings)

            # Add metadata
            for i, page in enumerate(pages):
                metadata.append({
                    "vec_id": start_id + i,
                    "page_num": page["page_num"],
                    "source_file": source_file,
                    "year": fiscal_year,
                    "text_preview": page["text"][:200],
                    "full_text": page["text"],
                    "quality": _score_page_quality(page["text"]),
                    "char_count": len(page["text"]),
                })

        # Save flat index (backward compat)
        faiss.write_index(index, faiss_path)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f)

        # Build scoped FAISS index for this report only
        if embeddings.shape[0] > 0:
            print(f"[AnnualReport] Building scoped index for {source_file}...")
            scoped_index = faiss.IndexFlatIP(dim)
            scoped_index.add(embeddings)
            faiss.write_index(scoped_index, _scoped_faiss_path(conv_id, source_file))

            scoped_meta = []
            for i, page in enumerate(pages):
                scoped_meta.append({
                    "vec_id": i,
                    "page_num": page["page_num"],
                    "source_file": source_file,
                    "year": fiscal_year,
                    "text_preview": page["text"][:200],
                    "full_text": page["text"],
                    "quality": _score_page_quality(page["text"]),
                    "char_count": len(page["text"]),
                })
            with open(_scoped_meta_path(conv_id, source_file), "w", encoding="utf-8") as f:
                json.dump(scoped_meta, f)
            print(f"[AnnualReport] Scoped index built: {len(scoped_meta)} pages")

        # Record in DB
        db.save_indexed_report(conv_id, symbol.upper(), symbol.upper(), from_yr, to_yr, file_url, total_pages, pdf_dest)

        print(f"[AnnualReport] Indexed {source_file}: {total_pages} pages")
        return {
            "pages_indexed": total_pages,
            "key_sections": toc_data.get("key_sections", []),
            "toc_preview": toc_data.get("toc_text", "")[:500],
            "pdf_path": pdf_dest,
            "sparse_pages_warning": sparse_pages > total_pages * 0.3,
        }


# ── Query ──

def get_index_summary(conv_id: str) -> str:
    """Human-readable summary of what's indexed."""
    reports = db.get_indexed_reports(conv_id)
    if not reports:
        return "No reports indexed yet."

    lines = []
    for r in reports:
        lines.append(f"- {r['symbol']} FY{r['from_yr']}-{r['to_yr']}: {r['page_count']} pages")
    return "Indexed reports:\n" + "\n".join(lines)


def get_pdf_index_pages(conv_id: str, symbol: str, year: str) -> dict:
    """Return the extracted TOC/index for a specific report."""
    # year can be "2023-2024" or just "2023"
    pattern_name = f"{symbol.upper()}_{year}"
    idx_dir = _pdf_index_dir(conv_id)

    # Try exact match first
    path = os.path.join(idx_dir, f"{pattern_name}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)

    # Try partial match
    import glob as globmod
    matches = globmod.glob(os.path.join(idx_dir, f"{symbol.upper()}_*{year}*.json"))
    if matches:
        with open(matches[0], "r") as f:
            return json.load(f)

    return {"toc_text": "", "key_sections": [], "error": "No TOC found for this report"}


def search_pages(conv_id: str, query: str, k: int = 3, scope_years: list = None, preview_only: bool = False) -> list:
    """FAISS semantic search. Optionally filter by year. Returns page results.
    If preview_only=True, skips full_text in results (cheaper for context)."""
    faiss_path_val = _faiss_path(conv_id)
    meta_path_val = _meta_path(conv_id)

    if not os.path.exists(faiss_path_val) or not os.path.exists(meta_path_val):
        return []

    index = faiss.read_index(faiss_path_val)
    with open(meta_path_val, "r") as f:
        metadata = json.load(f)

    if index.ntotal == 0:
        return []

    # Embed query
    client = _get_client()
    try:
        resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=[query[:8000]])
        q_vec = np.array([resp.data[0].embedding], dtype=np.float32)
        # Normalize
        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm
    except Exception as e:
        print(f"[AnnualReport] Query embedding failed: {e}")
        return []

    # Over-fetch if filtering by year
    fetch_k = min(k * 4, index.ntotal) if scope_years else min(k, index.ntotal)
    scores, indices = index.search(q_vec, fetch_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(metadata):
            continue
        meta = metadata[idx]

        # Year filter
        if scope_years:
            if not any(yr in meta.get("year", "") for yr in scope_years):
                continue

        entry = {
            "page_num": meta["page_num"],
            "source_file": meta["source_file"],
            "fiscal_year": meta["year"],
            "text_preview": meta["text_preview"],
            "quality": meta.get("quality", "good"),
            "char_count": meta.get("char_count", len(meta.get("full_text", ""))),
            "score": float(score),
        }
        if not preview_only:
            entry["text"] = meta["full_text"]
        results.append(entry)

        if len(results) >= k:
            break

    return results


def keyword_search(conv_id: str, keywords: list[str], k: int = 5, scope_years: list = None) -> list:
    """Regex keyword search across all page full_text in index_meta.json.
    Zero API calls, instant. Scores by keyword match count."""
    meta_path_val = _meta_path(conv_id)
    if not os.path.exists(meta_path_val):
        return []

    with open(meta_path_val, "r") as f:
        metadata = json.load(f)

    # Normalize keywords (in case old index still has ligatures)
    keywords = [_normalize_text(kw) for kw in keywords]
    # Build case-insensitive patterns
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords if kw]
    if not patterns:
        return []

    scored = []
    for meta in metadata:
        # Year filter
        if scope_years:
            if not any(yr in meta.get("year", "") for yr in scope_years):
                continue

        text = meta.get("full_text", "")
        match_count = sum(len(p.findall(text)) for p in patterns)
        if match_count > 0:
            scored.append((match_count, meta))

    # Sort by match count descending
    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for match_count, meta in scored[:k]:
        results.append({
            "page_num": meta["page_num"],
            "source_file": meta["source_file"],
            "fiscal_year": meta["year"],
            "text_preview": meta.get("text_preview", meta.get("full_text", "")[:200]),
            "quality": meta.get("quality", "good"),
            "char_count": meta.get("char_count", len(meta.get("full_text", ""))),
            "keyword_matches": match_count,
        })
    return results


def hybrid_search(conv_id: str, query: str, k: int = 5, scope_years: list = None) -> list:
    """Hybrid search: keyword first (free), FAISS fallback.
    Returns preview-only results."""
    # Extract keywords from query (skip stopwords)
    words = re.findall(r'[a-zA-Z]+', query.lower())
    keywords = [w for w in words if w not in _STOPWORDS and len(w) > 2]

    # Also match financial phrases present in the query
    query_lower = query.lower()
    for phrase in _FINANCIAL_PHRASES:
        if phrase in query_lower:
            keywords.append(phrase)

    # Step 1: keyword search
    kw_results = keyword_search(conv_id, keywords, k=k, scope_years=scope_years)

    if len(kw_results) >= k:
        return kw_results[:k]

    # Step 2: FAISS for remaining slots, deduplicate
    remaining = k - len(kw_results)
    seen = {(r["page_num"], r["source_file"]) for r in kw_results}

    faiss_results = search_pages(conv_id, query, k=remaining + len(seen), scope_years=scope_years, preview_only=True)

    combined = list(kw_results)
    for fr in faiss_results:
        key = (fr["page_num"], fr["source_file"])
        if key not in seen:
            seen.add(key)
            combined.append(fr)
            if len(combined) >= k:
                break

    return combined


def scoped_search(conv_id: str, source_file: str, query: str, k: int = 5) -> list:
    """Search within a single report's scoped FAISS index (V5-style).
    Falls back to flat index with source_file filter if scoped index doesn't exist."""
    scoped_faiss = _scoped_faiss_path(conv_id, source_file)
    scoped_meta = _scoped_meta_path(conv_id, source_file)

    if not os.path.exists(scoped_faiss) or not os.path.exists(scoped_meta):
        # Fallback: use flat index filtered by source_file
        print(f"[AnnualReport] No scoped index for {source_file}, using flat index")
        all_results = search_pages(conv_id, query, k=k * 2, preview_only=True)
        return [r for r in all_results if r["source_file"] == source_file][:k]

    index = faiss.read_index(scoped_faiss)
    with open(scoped_meta, "r") as f:
        metadata = json.load(f)

    if index.ntotal == 0:
        return []

    # Embed query
    client = _get_client()
    try:
        resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=[query[:8000]])
        q_vec = np.array([resp.data[0].embedding], dtype=np.float32)
        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm
    except Exception as e:
        print(f"[AnnualReport] Query embedding failed: {e}")
        return []

    fetch_k = min(k, index.ntotal)
    scores, indices = index.search(q_vec, fetch_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(metadata):
            continue
        meta = metadata[idx]
        results.append({
            "page_num": meta["page_num"],
            "source_file": meta["source_file"],
            "fiscal_year": meta["year"],
            "text_preview": meta["text_preview"],
            "quality": meta.get("quality", "good"),
            "char_count": meta.get("char_count", len(meta.get("full_text", ""))),
            "score": float(score),
        })

    return results


def scoped_keyword_search(conv_id: str, source_file: str, keywords: list[str], k: int = 5) -> list:
    """Keyword search scoped to a single report's metadata."""
    scoped_meta = _scoped_meta_path(conv_id, source_file)

    # Try scoped meta first, fall back to flat
    if os.path.exists(scoped_meta):
        with open(scoped_meta, "r") as f:
            metadata = json.load(f)
    else:
        meta_path_val = _meta_path(conv_id)
        if not os.path.exists(meta_path_val):
            return []
        with open(meta_path_val, "r") as f:
            metadata = [m for m in json.load(f) if m["source_file"] == source_file]

    keywords = [_normalize_text(kw) for kw in keywords]
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords if kw]
    if not patterns:
        return []

    scored = []
    for meta in metadata:
        text = meta.get("full_text", "")
        match_count = sum(len(p.findall(text)) for p in patterns)
        if match_count > 0:
            scored.append((match_count, meta))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for match_count, meta in scored[:k]:
        results.append({
            "page_num": meta["page_num"],
            "source_file": meta["source_file"],
            "fiscal_year": meta["year"],
            "text_preview": meta.get("text_preview", meta.get("full_text", "")[:200]),
            "quality": meta.get("quality", "good"),
            "char_count": meta.get("char_count", len(meta.get("full_text", ""))),
            "keyword_matches": match_count,
        })
    return results


def scoped_hybrid_search(conv_id: str, source_file: str, query: str, k: int = 5) -> list:
    """Hybrid search scoped to a single report: keyword first, FAISS fallback."""
    words = re.findall(r'[a-zA-Z]+', query.lower())
    keywords = [w for w in words if w not in _STOPWORDS and len(w) > 2]

    query_lower = query.lower()
    for phrase in _FINANCIAL_PHRASES:
        if phrase in query_lower:
            keywords.append(phrase)

    # Step 1: scoped keyword search
    kw_results = scoped_keyword_search(conv_id, source_file, keywords, k=k)

    if len(kw_results) >= k:
        return kw_results[:k]

    # Step 2: scoped FAISS for remaining
    remaining = k - len(kw_results)
    seen = {r["page_num"] for r in kw_results}

    faiss_results = scoped_search(conv_id, source_file, query, k=remaining + len(seen))

    combined = list(kw_results)
    for fr in faiss_results:
        if fr["page_num"] not in seen:
            seen.add(fr["page_num"])
            combined.append(fr)
            if len(combined) >= k:
                break

    return combined


def get_page(conv_id: str, page_num: int, source_file: str) -> dict:
    """Direct page lookup by number and source file."""
    meta_path_val = _meta_path(conv_id)
    if not os.path.exists(meta_path_val):
        return {"error": "No index found"}

    with open(meta_path_val, "r") as f:
        metadata = json.load(f)

    for meta in metadata:
        if meta["page_num"] == page_num and meta["source_file"] == source_file:
            return {
                "page_num": meta["page_num"],
                "source_file": meta["source_file"],
                "fiscal_year": meta["year"],
                "text": meta["full_text"],
                "text_preview": meta["text_preview"],
            }

    return {"error": f"Page {page_num} not found in {source_file}"}


def get_page_as_image(conv_id: str, page_num: int, source_file: str) -> Optional[str]:
    """Render a PDF page as a base64 PNG. Returns None if PDF not found."""
    # Find PDF path from indexed reports
    reports = db.get_indexed_reports(conv_id)
    for r in reports:
        expected = f"{r['symbol']}_{r['from_yr']}-{r['to_yr']}"
        if expected == source_file and r.get("pdf_path"):
            pdf_path = r["pdf_path"]
            if os.path.exists(pdf_path):
                try:
                    return _render_page_as_image(pdf_path, page_num)
                except Exception as e:
                    print(f"[AnnualReport] Vision render failed: {e}")
                    return None

    # Fallback: search pdf directory
    pdf_path = os.path.join(_pdf_dir(conv_id), f"{source_file}.pdf")
    if os.path.exists(pdf_path):
        try:
            return _render_page_as_image(pdf_path, page_num)
        except Exception as e:
            print(f"[AnnualReport] Vision render failed: {e}")
            return None

    return None


def cleanup_chat_data(conv_id: str):
    """Delete all chat data (FAISS index, PDFs, notes, etc.)."""
    chat_dir = os.path.join(config.CHAT_DATA_DIR, conv_id)
    if os.path.exists(chat_dir):
        shutil.rmtree(chat_dir)
    db.delete_indexed_reports(conv_id)
    # Clean up lock
    _conv_locks.pop(conv_id, None)
