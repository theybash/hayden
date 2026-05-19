"""
Test script for scoped RAG — run this to inspect what the agent sees.

Usage:
    cd backend
    python test_scoped_rag.py --conv-id f01b8c482c03

Tests:
1. Ligature normalization — does "profit" match on page 111?
2. Scoped index rebuild — creates per-report FAISS from existing flat index
3. Scoped search — finds P&L page within a specific report
4. TOC detection — finds index/TOC pages in the PDF
5. Vision TOC (optional) — extracts TOC via vision with page mapping

Add --vision flag to test vision TOC extraction (costs ~$0.02).
Add --reindex flag to rebuild the scoped index from the PDF (costs embedding API).
"""

import os
import sys
import json
import re
import argparse
import unicodedata

# Add backend to path
sys.path.insert(0, os.path.dirname(__file__))
import config

def normalize(text):
    return unicodedata.normalize("NFKC", text)


def test_ligature_fix(meta_path):
    """Test 1: Does normalizing text fix the ligature problem?"""
    print("\n" + "=" * 60)
    print("TEST 1: LIGATURE NORMALIZATION")
    print("=" * 60)

    with open(meta_path) as f:
        metadata = json.load(f)

    page_111 = [m for m in metadata if m["page_num"] == 111]
    if not page_111:
        print("  Page 111 not found in index")
        return

    raw_text = page_111[0]["full_text"]
    normalized = normalize(raw_text)

    # Check for ligature
    has_ligature = "ﬁ" in raw_text or "ﬂ" in raw_text
    print(f"  Raw text has ligatures: {has_ligature}")

    # Count "profit" matches before and after
    profit_raw = len(re.findall(r'profit', raw_text, re.IGNORECASE))
    profit_norm = len(re.findall(r'profit', normalized, re.IGNORECASE))
    print(f"  'profit' matches — raw: {profit_raw}, normalized: {profit_norm}")

    profit_loss_raw = len(re.findall(r'profit and loss', raw_text, re.IGNORECASE))
    profit_loss_norm = len(re.findall(r'profit and loss', normalized, re.IGNORECASE))
    print(f"  'profit and loss' — raw: {profit_loss_raw}, normalized: {profit_loss_norm}")

    financial_raw = len(re.findall(r'financial', raw_text, re.IGNORECASE))
    financial_norm = len(re.findall(r'financial', normalized, re.IGNORECASE))
    print(f"  'financial' — raw: {financial_raw}, normalized: {financial_norm}")

    if profit_norm > profit_raw:
        print("  ✅ Ligature normalization FIXES the search problem!")
    elif profit_raw > 0:
        print("  ✅ No ligature issue on this page (already works)")
    else:
        print("  ⚠️  'profit' not found even after normalization — page may not contain it")


def test_scoped_index(conv_id, source_file):
    """Test 2: Check if scoped index exists, show its structure."""
    print("\n" + "=" * 60)
    print("TEST 2: SCOPED INDEX STRUCTURE")
    print("=" * 60)

    scoped_dir = os.path.join(config.CHAT_DATA_DIR, conv_id, "scoped_indexes")
    scoped_faiss = os.path.join(scoped_dir, f"{source_file}.faiss")
    scoped_meta = os.path.join(scoped_dir, f"{source_file}_meta.json")

    if os.path.exists(scoped_faiss) and os.path.exists(scoped_meta):
        with open(scoped_meta) as f:
            meta = json.load(f)
        print(f"  ✅ Scoped index exists: {len(meta)} pages")
        qualities = {}
        for m in meta:
            q = m.get("quality", "good")
            qualities[q] = qualities.get(q, 0) + 1
        print(f"  Quality breakdown: {qualities}")
        print(f"  Total chars: {sum(m.get('char_count', 0) for m in meta):,}")
    else:
        print(f"  ❌ No scoped index found at {scoped_dir}")
        print(f"  Run with --reindex to build it, or re-index the report in the app")

    # Also show flat index for comparison
    flat_meta = os.path.join(config.CHAT_DATA_DIR, conv_id, "index_meta.json")
    if os.path.exists(flat_meta):
        with open(flat_meta) as f:
            meta = json.load(f)
        files = {}
        for m in meta:
            sf = m["source_file"]
            files[sf] = files.get(sf, 0) + 1
        print(f"\n  Flat index: {len(meta)} total pages across {len(files)} report(s)")
        for sf, count in files.items():
            print(f"    {sf}: {count} pages")


def test_keyword_search_comparison(conv_id, source_file):
    """Test 3: Compare keyword search with and without normalization."""
    print("\n" + "=" * 60)
    print("TEST 3: KEYWORD SEARCH (with normalization)")
    print("=" * 60)

    meta_path = os.path.join(config.CHAT_DATA_DIR, conv_id, "index_meta.json")
    if not os.path.exists(meta_path):
        print("  No index found")
        return

    with open(meta_path) as f:
        metadata = json.load(f)

    # Filter to source_file
    file_meta = [m for m in metadata if m["source_file"] == source_file]
    print(f"  Searching {len(file_meta)} pages in {source_file}")

    queries = [
        ("Consolidated Statement of Profit and Loss", ["consolidated", "statement", "profit", "loss", "profit and loss", "statement of profit"]),
        ("Balance Sheet", ["balance", "sheet", "balance sheet"]),
        ("Cash Flow Statement", ["cash", "flow", "cash flow"]),
        ("Revenue from Operations", ["revenue", "operations", "revenue from operations"]),
    ]

    for query_name, keywords in queries:
        print(f"\n  Query: \"{query_name}\"")
        keywords_norm = [normalize(kw) for kw in keywords]
        patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords_norm]

        scored = []
        for m in file_meta:
            text_norm = normalize(m.get("full_text", ""))
            count = sum(len(p.findall(text_norm)) for p in patterns)
            if count > 0:
                scored.append((count, m["page_num"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            top5 = scored[:5]
            print(f"    Top 5: {['p'+str(pg)+' ('+str(c)+' hits)' for c, pg in top5]}")
            # Check if page 111 is in results for P&L query
            if "profit" in query_name.lower():
                for i, (c, pg) in enumerate(scored):
                    if pg == 111:
                        print(f"    → Page 111 ranks #{i+1} with {c} matches ✅")
                        break
                else:
                    print(f"    → Page 111 not found ❌")
        else:
            print(f"    No results")


def test_toc_detection(conv_id, source_file):
    """Test 4: Detect TOC pages in the PDF."""
    print("\n" + "=" * 60)
    print("TEST 4: TOC PAGE DETECTION")
    print("=" * 60)

    meta_path = os.path.join(config.CHAT_DATA_DIR, conv_id, "index_meta.json")
    if not os.path.exists(meta_path):
        print("  No index found")
        return

    with open(meta_path) as f:
        metadata = json.load(f)

    file_meta = [m for m in metadata if m["source_file"] == source_file]

    # Simulate _detect_toc_pages using the metadata text
    pages_text = [{"page_num": m["page_num"], "text": normalize(m["full_text"])} for m in file_meta]

    toc_pages = []
    for p in pages_text[:12]:
        text = p["text"]
        lines = text.split("\n")
        if len(lines) < 3:
            continue
        num_ending_lines = sum(1 for line in lines if re.search(r'\d+\s*$', line.strip()) and len(line.strip()) > 10)
        dot_lines = sum(1 for line in lines if '...' in line or '●' in line)
        has_toc_keyword = bool(re.search(r'\b(index|contents|table of contents)\b', text.lower()))

        if has_toc_keyword and (num_ending_lines >= 5 or dot_lines >= 3):
            toc_pages.append(p["page_num"])
            print(f"  Page {p['page_num']}: TOC detected (keyword + {num_ending_lines} numbered lines)")
        elif num_ending_lines >= 8:
            toc_pages.append(p["page_num"])
            print(f"  Page {p['page_num']}: TOC detected ({num_ending_lines} numbered lines)")
        elif has_toc_keyword:
            print(f"  Page {p['page_num']}: has TOC keyword but only {num_ending_lines} numbered lines")

    if not toc_pages:
        print("  No TOC pages detected in first 12 pages")
    else:
        print(f"\n  Detected TOC pages: {toc_pages}")
        # Show the text of the first TOC page
        for m in file_meta:
            if m["page_num"] == toc_pages[0]:
                text = normalize(m["full_text"])
                print(f"\n  TOC page {toc_pages[0]} text (first 500 chars):")
                print(f"  {'─' * 50}")
                for line in text[:500].split('\n'):
                    print(f"    {line}")
                break


def test_vision_toc(conv_id, source_file):
    """Test 5: Extract TOC via vision (costs API call)."""
    print("\n" + "=" * 60)
    print("TEST 5: VISION-BASED TOC EXTRACTION")
    print("=" * 60)

    from services import annual_report_service

    meta_path = os.path.join(config.CHAT_DATA_DIR, conv_id, "index_meta.json")
    with open(meta_path) as f:
        metadata = json.load(f)

    file_meta = [m for m in metadata if m["source_file"] == source_file]
    pages_text = [{"page_num": m["page_num"], "text": normalize(m["full_text"])} for m in file_meta]

    toc_pages = annual_report_service._detect_toc_pages(pages_text)
    if not toc_pages:
        print("  No TOC pages detected, cannot run vision test")
        return

    # Find PDF path
    from services import db
    reports = db.get_indexed_reports(conv_id)
    pdf_path = None
    for r in reports:
        expected = f"{r['symbol']}_{r['from_yr']}-{r['to_yr']}"
        if expected == source_file:
            pdf_path = r.get("pdf_path")
            break

    if not pdf_path:
        pdf_path = os.path.join(config.CHAT_DATA_DIR, conv_id, "pdfs", f"{source_file}.pdf")

    if not os.path.exists(pdf_path):
        print(f"  PDF not found at {pdf_path}")
        return

    print(f"  Using TOC pages: {toc_pages}")
    print(f"  Sending to vision model ({config.SUB_AGENT_MODEL})...")

    toc_data = annual_report_service._extract_toc_with_vision(
        pdf_path, toc_pages, source_file.split("_")[0],
        "_".join(source_file.split("_")[1:]), len(file_meta)
    )

    print(f"\n  TOC text: {toc_data.get('toc_text', '')[:200]}")
    print(f"\n  Sections found: {len(toc_data.get('key_sections', []))}")
    for s in toc_data.get("key_sections", []):
        print(f"    {s.get('title', '?'):50s} doc_page={s.get('doc_page', '?'):>4}  →  pdf_page={s.get('pdf_page', '?'):>4}")

    page_map = toc_data.get("page_map", {})
    if page_map:
        print(f"\n  Page map sample (doc→pdf): ", end="")
        items = list(page_map.items())[:8]
        print(", ".join(f"{k}→{v}" for k, v in items))


def test_reindex(conv_id, source_file):
    """Rebuild scoped index from existing flat index (no API calls — just reorganizes data)."""
    print("\n" + "=" * 60)
    print("REBUILDING SCOPED INDEX FROM FLAT INDEX")
    print("=" * 60)

    import faiss
    import numpy as np

    meta_path = os.path.join(config.CHAT_DATA_DIR, conv_id, "index_meta.json")
    faiss_path = os.path.join(config.CHAT_DATA_DIR, conv_id, "index.faiss")

    if not os.path.exists(meta_path) or not os.path.exists(faiss_path):
        print("  No flat index found")
        return

    with open(meta_path) as f:
        metadata = json.load(f)

    index = faiss.read_index(faiss_path)
    print(f"  Flat index: {index.ntotal} vectors, {len(metadata)} metadata entries")

    # Filter to source_file
    file_indices = [i for i, m in enumerate(metadata) if m["source_file"] == source_file]
    if not file_indices:
        print(f"  No entries for {source_file}")
        return

    print(f"  Extracting {len(file_indices)} vectors for {source_file}...")

    # Reconstruct vectors for this file
    vectors = np.zeros((len(file_indices), index.d), dtype=np.float32)
    for new_idx, old_idx in enumerate(file_indices):
        vectors[new_idx] = index.reconstruct(old_idx)

    # Normalize text with ligature fix
    scoped_meta = []
    for new_idx, old_idx in enumerate(file_indices):
        m = metadata[old_idx]
        normalized_text = normalize(m.get("full_text", ""))
        normalized_preview = normalize(m.get("text_preview", ""))

        from services.annual_report_service import _score_page_quality
        scoped_meta.append({
            "vec_id": new_idx,
            "page_num": m["page_num"],
            "source_file": m["source_file"],
            "year": m["year"],
            "text_preview": normalized_preview[:200],
            "full_text": normalized_text,
            "quality": _score_page_quality(normalized_text),
            "char_count": len(normalized_text),
        })

    # Build scoped index
    scoped_index = faiss.IndexFlatIP(index.d)
    scoped_index.add(vectors)

    scoped_dir = os.path.join(config.CHAT_DATA_DIR, conv_id, "scoped_indexes")
    os.makedirs(scoped_dir, exist_ok=True)

    faiss.write_index(scoped_index, os.path.join(scoped_dir, f"{source_file}.faiss"))
    with open(os.path.join(scoped_dir, f"{source_file}_meta.json"), "w") as f:
        json.dump(scoped_meta, f)

    # Also update the flat index metadata with normalized text
    for old_idx in file_indices:
        metadata[old_idx]["full_text"] = normalize(metadata[old_idx].get("full_text", ""))
        metadata[old_idx]["text_preview"] = normalize(metadata[old_idx].get("text_preview", ""))[:200]
        metadata[old_idx]["quality"] = _score_page_quality(metadata[old_idx]["full_text"])
        metadata[old_idx]["char_count"] = len(metadata[old_idx]["full_text"])

    with open(meta_path, "w") as f:
        json.dump(metadata, f)

    qualities = {}
    for m in scoped_meta:
        q = m["quality"]
        qualities[q] = qualities.get(q, 0) + 1

    print(f"  ✅ Scoped index built: {len(scoped_meta)} pages")
    print(f"  Quality breakdown: {qualities}")
    print(f"  Flat index metadata also normalized")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test scoped RAG system")
    parser.add_argument("--conv-id", required=True, help="Conversation ID (folder name in chat_data)")
    parser.add_argument("--source-file", default=None, help="Source file e.g. KEC_2024-2025 (auto-detected if omitted)")
    parser.add_argument("--vision", action="store_true", help="Run vision TOC test (costs ~$0.02)")
    parser.add_argument("--reindex", action="store_true", help="Rebuild scoped index from flat index")
    args = parser.parse_args()

    conv_id = args.conv_id

    # Auto-detect source_file from metadata
    meta_path = os.path.join(config.CHAT_DATA_DIR, conv_id, "index_meta.json")
    if not os.path.exists(meta_path):
        print(f"ERROR: No index found at {meta_path}")
        sys.exit(1)

    with open(meta_path) as f:
        metadata = json.load(f)

    source_files = list(set(m["source_file"] for m in metadata))
    source_file = args.source_file or source_files[0]
    print(f"Testing conv_id={conv_id}, source_file={source_file}")
    print(f"Available reports: {source_files}")

    # Reindex first if requested
    if args.reindex:
        test_reindex(conv_id, source_file)

    # Run tests
    test_ligature_fix(meta_path)
    test_scoped_index(conv_id, source_file)
    test_keyword_search_comparison(conv_id, source_file)
    test_toc_detection(conv_id, source_file)

    if args.vision:
        test_vision_toc(conv_id, source_file)
    else:
        print("\n  (Skipping vision test — add --vision flag to enable)")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
