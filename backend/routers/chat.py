from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from openai import OpenAI
from services import db, annual_report_service, notes_service
from services.agent_service import run_main_agent, sse_event
import config
import uuid
import json

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Models ──

class SendMessageRequest(BaseModel):
    message: str
    context_toggles: dict = {}  # {"web_search": True, "announcements": True, "annual_reports": False}


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class IndexReportRequest(BaseModel):
    symbol: str
    from_yr: str
    to_yr: str
    file_url: str


class CreateNoteRequest(BaseModel):
    title: str
    content: str


# ── Conversation CRUD ──

@router.get("/conversations", response_model=List[ConversationOut])
async def list_conversations():
    return db.list_conversations()


@router.post("/conversations", response_model=ConversationOut)
async def create_conversation():
    conv_id = uuid.uuid4().hex[:12]
    return db.create_conversation(conv_id)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    db.delete_conversation(conv_id)
    annual_report_service.cleanup_chat_data(conv_id)
    return {"ok": True}


@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: str):
    return db.get_messages(conv_id)


# ── Context fetchers (used by both simple flow and agent flow) ──

def _fetch_web_context(query: str) -> str:
    """Use GPT with web search to get live context."""
    if not config.OPENAI_API_KEY:
        return ""
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.responses.create(
            model=config.NEWS_MODEL,
            tools=[{"type": "web_search_preview"}],
            instructions="You are a research assistant. Search the web and return factual, concise findings. Focus on recent data, prices, and news.",
            input=query,
        )
        return response.output_text.strip()
    except Exception as e:
        print(f"[Chat] Web search failed: {e}")
        return ""


def _fetch_announcements_context(query: str) -> str:
    """Pull relevant announcements from our news DB."""
    words = query.upper().split()
    potential_symbols = [w.strip(",.?!()") for w in words if w.strip(",.?!()").isalpha() and len(w.strip(",.?!()")) >= 3 and w.strip(",.?!()") == w.strip(",.?!()").upper()]

    items = []
    if potential_symbols:
        items = db.get_news_by_symbols(potential_symbols[:5])

    if not items:
        recent = db.load_recent(hours=48)
        items = [
            {"headline": n.headline, "source_name": n.source_name, "url": n.url,
             "timestamp": n.timestamp.isoformat(), "impact_score": n.impact_score,
             "symbol": n.symbol, "description": n.description}
            for n in sorted(recent, key=lambda x: x.impact_score, reverse=True)[:15]
        ]

    if not items:
        return ""

    lines = []
    for item in items[:20]:
        line = f"- [{item.get('impact_score', 0)}] {item.get('headline', '')}"
        if item.get("symbol"):
            line += f" [{item['symbol']}]"
        if item.get("source_name"):
            line += f" ({item['source_name']})"
        if item.get("url"):
            line += f" | {item['url']}"
        lines.append(line)

    return "Recent announcements & news:\n" + "\n".join(lines)


def _build_context(query: str, toggles: dict) -> str:
    """Build context string from enabled sources (non-agentic flow)."""
    parts = []

    if toggles.get("web_search"):
        web = _fetch_web_context(query)
        if web:
            parts.append(f"## Web Search Results\n{web}")

    if toggles.get("announcements"):
        ann = _fetch_announcements_context(query)
        if ann:
            parts.append(f"## News & Announcements\n{ann}")

    return "\n\n".join(parts)


# ── Chat endpoint (now with SSE streaming) ──

SYSTEM_PROMPT = """You are Hayden, an expert Indian stock market research assistant.

Rules:
- Be direct and specific. No fluff.
- When you cite news or data, reference the source.
- Explain financial jargon in [brackets] the first time you use it.
- If context is provided below your instructions, USE it to ground your answers in real data.
- If you don't have enough information to answer confidently, say so.
- For stock analysis, consider: recent news, fundamentals, sector trends, and macro factors."""


@router.post("/conversations/{conv_id}/send")
async def send_message(conv_id: str, req: SendMessageRequest):
    """Send a message and get a streamed response via SSE.
    When annual_reports toggle is on → agentic flow.
    Otherwise → simple LLM flow (still streamed)."""

    # Ensure conversation exists
    convos = db.list_conversations()
    if not any(c["id"] == conv_id for c in convos):
        db.create_conversation(conv_id)

    # Save user message
    db.add_message(conv_id, "user", req.message)

    # Get history
    history = db.get_messages(conv_id)

    # Auto-title on first exchange
    if len(history) <= 1:
        _auto_title(conv_id, req.message)

    # Decide flow
    if req.context_toggles.get("annual_reports"):
        # Agentic flow — SSE streaming
        return StreamingResponse(
            _agentic_stream(conv_id, req.message, req.context_toggles, history),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # Simple flow — still SSE for consistency
        return StreamingResponse(
            _simple_stream(conv_id, req.message, req.context_toggles, history),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )


async def _agentic_stream(conv_id, message, toggles, history):
    """Agentic flow: main agent orchestrates sub-agents, streams SSE events."""
    full_answer = ""
    async for event in run_main_agent(conv_id, message, toggles, history):
        yield event
        # Capture the final answer for DB storage
        if "event: answer" in event:
            try:
                data_line = event.split("data: ", 1)[1].strip()
                data = json.loads(data_line)
                full_answer = data.get("content", "")
            except (IndexError, json.JSONDecodeError):
                pass

    # Save assistant reply
    if full_answer:
        context_sources = json.dumps({**toggles, "mode": "agentic"})
        db.add_message(conv_id, "assistant", full_answer, context_sources)

    yield sse_event("done", {"done": True})


async def _simple_stream(conv_id, message, toggles, history):
    """Simple flow: build context, call LLM, stream the response."""
    yield sse_event("thinking", {"cycle": 1, "max": 1})

    context = _build_context(message, toggles)
    context_sources = json.dumps(toggles)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": f"Context for this conversation:\n\n{context}"})

    for msg in history:
        if msg["role"] in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})

    if not config.OPENAI_API_KEY:
        reply = "OpenAI API key not configured."
        yield sse_event("answer", {"content": reply, "done": True})
        db.add_message(conv_id, "assistant", reply, context_sources)
        return

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        client = OpenAI(api_key=config.OPENAI_API_KEY)

        # Run blocking OpenAI call in executor so we don't block the event loop
        stream_obj = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=config.CHAT_MODEL,
                messages=messages,
                stream=True,
            ),
        )

        # Read all stream chunks in executor, then yield them
        def _read_stream():
            chunks = []
            for chunk in stream_obj:
                delta = chunk.choices[0].delta
                if delta.content:
                    chunks.append(delta.content)
            return chunks

        stream_chunks = await loop.run_in_executor(None, _read_stream)
        full_reply = ""
        for token in stream_chunks:
            full_reply += token
            yield sse_event("stream", {"token": token})

        yield sse_event("answer", {"content": full_reply, "done": True})
        db.add_message(conv_id, "assistant", full_reply, context_sources)

    except Exception as e:
        error_msg = f"Error: {str(e)}"
        yield sse_event("answer", {"content": error_msg, "done": True})
        db.add_message(conv_id, "assistant", error_msg, context_sources)

    yield sse_event("done", {"done": True})


# ── Annual Report endpoints ──

@router.get("/conversations/{conv_id}/reports")
async def list_indexed_reports(conv_id: str):
    """List all indexed reports for this conversation."""
    return db.get_indexed_reports(conv_id)


@router.post("/conversations/{conv_id}/index-report")
async def index_report(conv_id: str, req: IndexReportRequest):
    """Manually trigger report indexing."""
    result = annual_report_service.download_and_index(
        conv_id, req.symbol, req.from_yr, req.to_yr, req.file_url
    )
    return result


@router.get("/reports/{symbol}")
async def fetch_report_list(symbol: str):
    """Get available annual reports from NSE for a symbol."""
    return annual_report_service.fetch_report_list(symbol)


# ── Notes endpoints ──

@router.get("/conversations/{conv_id}/notes")
async def list_notes(conv_id: str):
    return notes_service.list_notes(conv_id)


@router.post("/conversations/{conv_id}/notes")
async def create_note(conv_id: str, req: CreateNoteRequest):
    return notes_service.create_note(conv_id, req.title, req.content)


# ── Helpers ──

def _auto_title(conv_id: str, first_message: str):
    """Generate a short title from the first message."""
    if not config.OPENAI_API_KEY:
        return
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=config.SCORING_MODEL,
            messages=[
                {"role": "system", "content": "Generate a 3-5 word title for this chat. Return ONLY the title, nothing else."},
                {"role": "user", "content": first_message[:200]},
            ],
        )
        title = (response.choices[0].message.content or "").strip().strip('"')
        if title:
            db.update_conversation_title(conv_id, title)
    except Exception:
        pass
