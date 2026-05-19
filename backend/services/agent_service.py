"""Agentic orchestration v2 — Smart RAG with direct search.
Main agent (GPT-5) gets direct RAG tools. Sub-agents (GPT-4.1) are for complex queries only.
Hybrid search (keyword first, FAISS fallback), context truncation, forced finish."""

import re
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

from openai import OpenAI

import config
from services import annual_report_service, notes_service

_executor = ThreadPoolExecutor(max_workers=config.MAX_SUB_AGENTS)


def _get_client() -> OpenAI:
    return OpenAI(api_key=config.OPENAI_API_KEY)


def sse_event(event: str, data: dict | str) -> str:
    """Format an SSE event."""
    if isinstance(data, str):
        data = {"content": data}
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ── Token Tracking ──

class TokenTracker:
    """Accumulates token usage across multiple LLM calls."""
    def __init__(self):
        self.main_prompt = 0
        self.main_completion = 0
        self.main_calls = 0
        self.sub_prompt = 0
        self.sub_completion = 0
        self.sub_calls = 0
        self.embedding_tokens = 0
        self.vision_calls = 0

    def add_main(self, usage):
        if usage:
            self.main_prompt += getattr(usage, 'prompt_tokens', 0)
            self.main_completion += getattr(usage, 'completion_tokens', 0)
            self.main_calls += 1

    def add_sub(self, usage):
        if usage:
            self.sub_prompt += getattr(usage, 'prompt_tokens', 0)
            self.sub_completion += getattr(usage, 'completion_tokens', 0)
            self.sub_calls += 1

    def add_vision(self):
        self.vision_calls += 1

    def _estimate_cost(self) -> float:
        """Rough USD cost estimate based on token counts + vision calls."""
        # Approximate pricing (per 1M tokens)
        main_input_rate = 10.0   # GPT-5 input
        main_output_rate = 30.0  # GPT-5 output
        sub_input_rate = 2.0     # GPT-4.1 input
        sub_output_rate = 8.0    # GPT-4.1 output
        vision_cost = 0.01       # per vision call (rough)

        cost = (
            (self.main_prompt / 1_000_000) * main_input_rate +
            (self.main_completion / 1_000_000) * main_output_rate +
            (self.sub_prompt / 1_000_000) * sub_input_rate +
            (self.sub_completion / 1_000_000) * sub_output_rate +
            self.vision_calls * vision_cost
        )
        return round(cost, 4)

    def to_dict(self):
        return {
            "main_agent": {
                "model": config.MAIN_AGENT_MODEL,
                "calls": self.main_calls,
                "prompt_tokens": self.main_prompt,
                "completion_tokens": self.main_completion,
                "total_tokens": self.main_prompt + self.main_completion,
            },
            "sub_agents": {
                "model": config.SUB_AGENT_MODEL,
                "calls": self.sub_calls,
                "prompt_tokens": self.sub_prompt,
                "completion_tokens": self.sub_completion,
                "total_tokens": self.sub_prompt + self.sub_completion,
            },
            "vision_calls": self.vision_calls,
            "total_tokens": (self.main_prompt + self.main_completion +
                           self.sub_prompt + self.sub_completion),
            "estimated_cost_usd": self._estimate_cost(),
        }


# ── Context Compression ──

def _summarize_long_text(text: str, page_num: int, source_file: str, max_chars: int = config.TOOL_RESULT_CAP) -> str:
    """Use a cheap model to summarize oversized page text, preserving all key data."""
    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=config.SCORING_MODEL,  # gpt-4.1-mini — cheap and fast
            messages=[
                {"role": "system", "content": (
                    "You are a financial data extractor. Summarize this annual report page into a dense, "
                    "information-packed summary under 1500 characters. PRESERVE ALL:\n"
                    "- Exact numbers, amounts, percentages, ratios\n"
                    "- Table data (reproduce key rows/columns in compact format)\n"
                    "- Company names, dates, fiscal years\n"
                    "- Section headings and categories\n"
                    "Drop boilerplate, disclaimers, and filler text. If it's a financial table, "
                    "keep ALL line items with their values."
                )},
                {"role": "user", "content": f"Page {page_num} from {source_file}:\n\n{text}"},
            ],
            max_tokens=800,
        )
        summary = resp.choices[0].message.content or ""
        return summary[:max_chars]
    except Exception as e:
        # Fallback to hard truncation if summarization fails
        print(f"[AgentService] Summarization failed, falling back to truncation: {e}")
        return text[:max_chars] + f"... [TRUNCATED from {len(text)} chars]"


def _truncate_tool_result_sync(tool_name: str, result: dict, max_chars: int = config.TOOL_RESULT_CAP) -> str:
    """Synchronous version — used inside sub-agent threads where blocking is OK.
    Compresses tool results using LLM summarization for large pages."""
    return _do_truncate(tool_name, result, max_chars)


async def _truncate_tool_result_async(tool_name: str, result: dict, max_chars: int = config.TOOL_RESULT_CAP) -> str:
    """Async version — used in main agent loop to avoid blocking the event loop.
    Runs summarization in a thread executor if needed."""
    text = json.dumps(result)
    needs_summarization = (
        (tool_name == "get_page" and "text" in result and len(result["text"]) > max_chars) or
        (tool_name == "read_page_as_image" and "vision_analysis" in result and len(result["vision_analysis"]) > max_chars) or
        (tool_name == "spawn_sub_agents" and "sub_agent_results" in result and
         any(len(r.get("findings") or "") > 600 for r in result["sub_agent_results"]))
    )
    if needs_summarization:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_truncate, tool_name, result, max_chars)
    return _do_truncate(tool_name, result, max_chars)


def _do_truncate(tool_name: str, result: dict, max_chars: int = config.TOOL_RESULT_CAP) -> str:
    """Core truncation logic. Compresses tool results using LLM summarization
    for large pages to preserve key financial data instead of blindly cutting text."""
    if tool_name == "rag_search":
        # Already preview-only, just cap total
        text = json.dumps(result)
        if len(text) > max_chars:
            return text[:max_chars] + '..."truncated"}'
        return text

    elif tool_name == "get_page":
        if "text" in result and len(result["text"]) > max_chars:
            compressed = dict(result)
            compressed["text"] = _summarize_long_text(
                result["text"], result.get("page_num", 0), result.get("source_file", "")
            )
            compressed["_compressed"] = True
            return json.dumps(compressed)
        return json.dumps(result)

    elif tool_name == "spawn_sub_agents":
        # Sub-agent findings are already extracted facts — just slim the structure
        if "sub_agent_results" in result:
            slim = []
            for r in result["sub_agent_results"]:
                findings = r.get("findings") or ""
                # Summarize oversized sub-agent findings too
                if len(findings) > 600:
                    findings = _summarize_long_text(
                        findings, 0, f"sub-agent-{r.get('agent_id', '?')}"
                    )[:600]
                slim.append({
                    "agent_id": r.get("agent_id"),
                    "status": r.get("status"),
                    "findings": findings,
                    "pages_consulted": r.get("pages_consulted", []),
                })
            return json.dumps({"sub_agent_results": slim})
        return json.dumps(result)[:max_chars]

    elif tool_name == "read_page_as_image":
        if "vision_analysis" in result and len(result["vision_analysis"]) > max_chars:
            compressed = dict(result)
            compressed["vision_analysis"] = _summarize_long_text(
                result["vision_analysis"], result.get("page_num", 0), result.get("source_file", "")
            )
            return json.dumps(compressed)
        return json.dumps(result)

    else:
        text = json.dumps(result)
        if len(text) > max_chars:
            return text[:max_chars] + '..."truncated"}'
        return text


# ── Tool Definitions for OpenAI function calling ──

# RAG tools shared by main agent and sub-agents
_RAG_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "rag_search",
        "description": "Search within a specific report's scoped index. Hybrid: keyword first (free), FAISS fallback. Returns page PREVIEWS (200 chars) + quality scores. Use get_page() to read full text.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for annual report pages"},
                "source_file": {"type": "string", "description": "Report to search in, e.g. 'KEC_2024-2025'. If omitted, searches all reports."},
                "k": {"type": "integer", "default": 5, "description": "Number of results to return"},
            },
            "required": ["query"]
        }
    }
}

_GET_PAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_page",
        "description": "Read full text of a specific page by number from an indexed report.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_num": {"type": "integer"},
                "source_file": {"type": "string", "description": "e.g. KEC_2024-2025"}
            },
            "required": ["page_num", "source_file"]
        }
    }
}

_READ_PAGE_AS_IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_page_as_image",
        "description": "Vision wildcard: render PDF page as image and analyze with AI. You have 4 uses max. Use when page quality is 'bad', text is garbled, or you need to verify a table visually.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_num": {"type": "integer"},
                "source_file": {"type": "string", "description": "e.g. KEC_2024-2025"},
                "question": {"type": "string", "description": "What to look for in this page image"}
            },
            "required": ["page_num", "source_file", "question"]
        }
    }
}

MAIN_AGENT_TOOLS = [
    _RAG_SEARCH_TOOL,
    _GET_PAGE_TOOL,
    _READ_PAGE_AS_IMAGE_TOOL,
    {
        "type": "function",
        "function": {
            "name": "fetch_report_list",
            "description": "Get available annual reports from NSE for a listed company. Returns list with company_name, fromYr, toYr, file_url.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE stock symbol (e.g. RELIANCE, TCS, INFY)"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "index_report",
            "description": "Download and index an annual report. Returns page count + extracted table of contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "from_yr": {"type": "string", "description": "Start year e.g. '2023'"},
                    "to_yr": {"type": "string", "description": "End year e.g. '2024'"},
                    "file_url": {"type": "string", "description": "URL from fetch_report_list"}
                },
                "required": ["symbol", "from_yr", "to_yr", "file_url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_index_summary",
            "description": "See what reports are already indexed — companies, years, page counts.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_pdf_index",
            "description": "Read the TABLE OF CONTENTS of an indexed report. Shows section titles and page numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "year": {"type": "string", "description": "Fiscal year e.g. '2023-2024'"}
                },
                "required": ["symbol", "year"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_sub_agents",
            "description": "Launch up to 3 research sub-agents in parallel. COMPLEX QUESTIONS ONLY — for simple lookups use rag_search + get_page directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "Search query for the annual report"},
                                "scope_years": {"type": "array", "items": {"type": "string"}, "description": "Which fiscal years to search"},
                                "scope_pages": {"type": "array", "items": {"type": "integer"}, "description": "Specific page numbers to look at"},
                                "objective": {"type": "string", "description": "One-line description of what to find"}
                            },
                            "required": ["query", "objective"]
                        },
                        "maxItems": 3
                    }
                },
                "required": ["tasks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "Save research findings as a markdown note for future reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Deliver your final answer to the user. Call this when you have enough information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "Your complete final answer with citations"}
                },
                "required": ["answer"]
            }
        }
    },
]

# Sub-agent tools (subset: RAG + finish only)
SUB_AGENT_TOOLS = [
    _RAG_SEARCH_TOOL,
    _GET_PAGE_TOOL,
    _READ_PAGE_AS_IMAGE_TOOL,
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Declare done. Status is 'pass' (found data) or 'fail' (couldn't find it). Include extracted facts with page numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["pass", "fail"]},
                    "findings": {"type": "string"}
                },
                "required": ["status", "findings"]
            }
        }
    },
]


# ── Prompts ──

MAIN_AGENT_PROMPT = """You are Hayden, an expert Indian stock market research analyst.

## CRITICAL RULES
1. SIMPLE QUESTIONS = DIRECT SEARCH. "Show me income statement", "what was revenue"
   → rag_search(query, source_file) → get_page → finish. 2 cycles. Do NOT spawn sub-agents.
2. SUB-AGENTS = COMPLEX ONLY. "Compare 3 years of margins across segments"
   → spawn up to 3 sub-agents with focused tasks.
3. FINISH FAST. Target: 1-3 cycles for simple, 4-6 for complex. 7+ = inefficient.
4. ALWAYS SCOPE YOUR SEARCH. Use source_file parameter in rag_search to search within
   a specific report. Each report has its own scoped index — this is far more accurate
   than searching the global index.
5. VISION WILDCARDS. You have 4 vision calls total. Use read_page_as_image when:
   - Page quality is "bad" or text is garbled (<50 chars)
   - You need to verify a financial table visually
   - Text extraction missed important data

## Tools (order of preference)
1. rag_search(query, source_file, k) — Scoped hybrid search within a report.
   Returns page PREVIEWS (200 chars) + quality scores. Read full pages with get_page.
   ALWAYS pass source_file when you know which report to search.
2. get_page(page_num, source_file) — Full page text.
3. read_page_as_image(page_num, source_file, question) — Vision wildcard (4 max).
4. get_pdf_index(symbol, year) — TOC with doc_page→pdf_page mapping. Check pdf_page field.
5. fetch_report_list / index_report / get_index_summary — Report management.
6. spawn_sub_agents(tasks[]) — Up to 3 parallel agents. Complex questions ONLY.
7. create_note / finish

## Workflow: Simple Question
Cycle 1: rag_search(query, source_file="KEC_2024-2025") → scan previews → get_page for best match
Cycle 2: finish(answer with page citations)

## Workflow: Complex Question
Cycle 1: get_index_summary, index if needed
Cycle 2: get_pdf_index for TOC (has pdf_page numbers), plan sub-agent tasks
Cycle 3: spawn_sub_agents (max 3 focused tasks)
Cycle 4: synthesize results → finish

{symbols_note}

## Currently Indexed
{index_summary}"""

SUB_AGENT_PROMPT = """You are a focused research sub-agent. ONE job: {task_objective}

## HARD RULES
1. MAX {max_cycles} CYCLES. Finish in 1-2. Cycle {max_cycles} = forced termination.
2. Call finish() THE MOMENT you have data. Do not keep searching.
3. rag_search returns PREVIEWS. Call get_page() for full text.
4. ALWAYS pass source_file to rag_search to search within the specific report.
5. Vision wildcards: use read_page_as_image when text is bad or you need to verify a table.
6. Findings = EXTRACTED FACTS with page numbers. Not raw page dumps.

## Tools
1. rag_search(query, source_file, k) — Scoped search. ALWAYS pass source_file.
2. get_page(page_num, source_file) — Full text of a page.
3. read_page_as_image(page_num, source_file, question) — Vision wildcard.
4. finish(status, findings) — "pass" + data, or "fail" + what you tried.

## Example
Cycle 1: rag_search("net profit", source_file="KEC_2024-2025") → page 111 relevant → get_page(111, "KEC_2024-2025")
Cycle 2: finish("pass", "Net profit FY25: ₹823 Cr (page 111)")"""


# ── Vision Execution (shared by main agent and sub-agents) ──

def _execute_vision(client: OpenAI, conv_id: str, page_num: int, source_file: str,
                    question: str, tracker: TokenTracker) -> str:
    """Render page as image, send to vision model, return JSON result string."""
    img_b64 = annual_report_service.get_page_as_image(conv_id, page_num, source_file)
    if not img_b64:
        return json.dumps({"error": "PDF not found or page render failed."})

    try:
        vision_resp = client.chat.completions.create(
            model=config.SUB_AGENT_MODEL,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": f"This is page {page_num} from annual report {source_file}. {question}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"}},
                ]},
            ],
            max_tokens=2000,
        )
        tracker.add_sub(vision_resp.usage)
        tracker.add_vision()
        return json.dumps({
            "page_num": page_num,
            "source_file": source_file,
            "vision_analysis": vision_resp.choices[0].message.content,
        })
    except Exception as e:
        return json.dumps({"error": f"Vision call failed: {str(e)}"})


# ── Tool Execution ──

async def _execute_main_tool(tool_call, conv_id: str, sub_agent_events: list,
                              tracker: TokenTracker, vision_calls_used: int) -> tuple[dict, int]:
    """Execute a main agent tool call. Returns (result_dict, updated_vision_calls_used)."""
    loop = asyncio.get_event_loop()
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

    if name == "rag_search":
        source_file = args.get("source_file")
        if source_file:
            # Scoped search within a specific report
            result = await loop.run_in_executor(
                None, lambda: annual_report_service.scoped_hybrid_search(
                    conv_id, source_file,
                    args.get("query", ""),
                    k=args.get("k", 5),
                )
            )
        else:
            # Global search across all reports
            result = await loop.run_in_executor(
                None, lambda: annual_report_service.hybrid_search(
                    conv_id,
                    args.get("query", ""),
                    k=args.get("k", 5),
                )
            )
        return {"results": result, "count": len(result)}, vision_calls_used

    elif name == "get_page":
        result = await loop.run_in_executor(
            None, lambda: annual_report_service.get_page(
                conv_id, args["page_num"], args["source_file"]
            )
        )
        return result, vision_calls_used

    elif name == "read_page_as_image":
        if vision_calls_used >= config.MAX_VISION_CALLS:
            return {"error": f"Vision limit reached ({config.MAX_VISION_CALLS}). No wildcards remaining. Use text tools."}, vision_calls_used
        client = _get_client()
        result_str = await loop.run_in_executor(
            None, lambda: _execute_vision(
                client, conv_id, args["page_num"], args["source_file"],
                args.get("question", "What information is on this page?"), tracker
            )
        )
        result = json.loads(result_str)
        new_used = vision_calls_used + 1
        result["vision_wildcards_remaining"] = config.MAX_VISION_CALLS - new_used
        return result, new_used

    elif name == "fetch_report_list":
        result = await loop.run_in_executor(None, annual_report_service.fetch_report_list, args["symbol"])
        return {"reports": result, "count": len(result)}, vision_calls_used

    elif name == "index_report":
        result = await loop.run_in_executor(
            None, annual_report_service.download_and_index,
            conv_id, args["symbol"], args["from_yr"], args["to_yr"], args["file_url"]
        )
        return result, vision_calls_used

    elif name == "get_index_summary":
        summary = await loop.run_in_executor(None, annual_report_service.get_index_summary, conv_id)
        return {"summary": summary}, vision_calls_used

    elif name == "get_pdf_index":
        result = await loop.run_in_executor(
            None, annual_report_service.get_pdf_index_pages, conv_id, args["symbol"], args["year"]
        )
        return result, vision_calls_used

    elif name == "spawn_sub_agents":
        tasks = args.get("tasks", [])[:config.MAX_SUB_AGENTS]

        sub_agent_events.append(sse_event("sub_agents_spawned", {
            "count": len(tasks),
            "tasks": [t["objective"] for t in tasks],
        }))

        loop = asyncio.get_event_loop()
        futures = []
        for i, task in enumerate(tasks):
            futures.append(
                loop.run_in_executor(
                    _executor, _run_sub_agent, task, conv_id, i, sub_agent_events, tracker,
                )
            )

        results = await asyncio.gather(*futures, return_exceptions=True)

        agent_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                agent_results.append({
                    "agent_id": i, "status": "fail",
                    "findings": f"Sub-agent error: {str(result)}",
                    "cycles_used": 0, "pages_consulted": [], "vision_calls_used": 0,
                })
            else:
                agent_results.append(result)

        return {"sub_agent_results": agent_results}, vision_calls_used

    elif name == "create_note":
        return notes_service.create_note(conv_id, args["title"], args["content"]), vision_calls_used

    elif name == "finish":
        return {"answer": args.get("answer", "")}, vision_calls_used

    return {"error": f"Unknown tool: {name}"}, vision_calls_used


def _run_sub_agent(task: dict, conv_id: str, agent_id: int, events: list, tracker: TokenTracker) -> dict:
    """Run a sub-agent (blocking, runs in thread). Up to MAX_SUB_CYCLES cycles with force-finish."""
    client = _get_client()
    vision_calls_used = 0
    pages_consulted = []
    max_cycles = config.MAX_SUB_CYCLES
    accumulated_findings = []

    system_prompt = SUB_AGENT_PROMPT.replace("{task_objective}", task["objective"])
    system_prompt = system_prompt.replace("{max_cycles}", str(max_cycles))

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Task: {task['objective']}\nSearch query: {task['query']}\nScope years: {json.dumps(task.get('scope_years', []))}\nScope pages: {json.dumps(task.get('scope_pages', []))}"},
    ]

    for cycle in range(max_cycles):
        events.append(sse_event("sub_agent_progress", {
            "agent_id": agent_id,
            "cycle": cycle + 1,
            "max_cycles": max_cycles,
            "status": "thinking",
            "objective": task["objective"],
        }))

        # Force-finish injection on last cycle
        if cycle == max_cycles - 1:
            messages.append({
                "role": "system",
                "content": "FINAL CYCLE. You MUST call finish() now. Summarize what you found.",
            })

        try:
            response = client.chat.completions.create(
                model=config.SUB_AGENT_MODEL,
                messages=messages,
                tools=SUB_AGENT_TOOLS,
            )
            tracker.add_sub(response.usage)
        except Exception as e:
            return {
                "agent_id": agent_id, "status": "fail",
                "findings": f"LLM error: {str(e)}",
                "cycles_used": cycle + 1, "pages_consulted": pages_consulted,
                "vision_calls_used": vision_calls_used,
            }

        msg = response.choices[0].message

        if not msg.tool_calls:
            # No tools — treat as done
            findings = msg.content or "No findings"
            events.append(sse_event("sub_agent_done", {
                "agent_id": agent_id, "status": "pass",
                "cycles_used": cycle + 1, "summary": findings[:300],
                "vision_calls_used": vision_calls_used,
            }))
            return {
                "agent_id": agent_id, "status": "pass",
                "findings": findings, "cycles_used": cycle + 1,
                "pages_consulted": pages_consulted, "vision_calls_used": vision_calls_used,
            }

        # Process tool calls
        messages.append(msg)
        finished = False
        for tc in msg.tool_calls:
            tc_name = tc.function.name
            tc_args = json.loads(tc.function.arguments) if tc.function.arguments else {}

            if tc_name == "rag_search":
                source_file = tc_args.get("source_file")
                if source_file:
                    result = annual_report_service.scoped_hybrid_search(
                        conv_id, source_file,
                        tc_args.get("query", ""),
                        k=tc_args.get("k", 5),
                    )
                else:
                    result = annual_report_service.hybrid_search(
                        conv_id, tc_args.get("query", ""),
                        k=tc_args.get("k", 5),
                    )
                for r in result:
                    if r["page_num"] not in pages_consulted:
                        pages_consulted.append(r["page_num"])
                events.append(sse_event("sub_agent_progress", {
                    "agent_id": agent_id, "cycle": cycle + 1,
                    "status": "searching", "query": tc_args.get("query", ""),
                    "results_count": len(result),
                }))
                tool_result = _truncate_tool_result_sync("rag_search", {"results": result, "count": len(result)})

            elif tc_name == "get_page":
                result = annual_report_service.get_page(
                    conv_id, tc_args["page_num"], tc_args["source_file"]
                )
                if tc_args["page_num"] not in pages_consulted:
                    pages_consulted.append(tc_args["page_num"])
                # Track any text we got for force-finish
                if result.get("text"):
                    accumulated_findings.append(f"Page {tc_args['page_num']} ({tc_args['source_file']}): {result['text'][:300]}")
                events.append(sse_event("sub_agent_progress", {
                    "agent_id": agent_id, "cycle": cycle + 1,
                    "status": "reading_page", "page": tc_args["page_num"],
                    "source": tc_args["source_file"],
                }))
                tool_result = _truncate_tool_result_sync("get_page", result)

            elif tc_name == "read_page_as_image":
                if vision_calls_used >= config.MAX_VISION_CALLS:
                    tool_result = json.dumps({"error": f"Vision limit reached ({config.MAX_VISION_CALLS}). No wildcards remaining."})
                else:
                    tool_result = _execute_vision(
                        client, conv_id, tc_args["page_num"], tc_args["source_file"],
                        tc_args.get("question", "What information is on this page?"), tracker
                    )
                    vision_calls_used += 1
                    result_parsed = json.loads(tool_result)
                    result_parsed["vision_wildcards_remaining"] = config.MAX_VISION_CALLS - vision_calls_used
                    if result_parsed.get("vision_analysis"):
                        accumulated_findings.append(f"Vision page {tc_args['page_num']}: {result_parsed['vision_analysis'][:300]}")
                    tool_result = _truncate_tool_result_sync("read_page_as_image", result_parsed)

                if tc_args["page_num"] not in pages_consulted:
                    pages_consulted.append(tc_args["page_num"])
                events.append(sse_event("sub_agent_progress", {
                    "agent_id": agent_id, "cycle": cycle + 1,
                    "status": "vision_read", "page": tc_args["page_num"],
                    "vision_calls_used": vision_calls_used,
                }))

            elif tc_name == "finish":
                events.append(sse_event("sub_agent_done", {
                    "agent_id": agent_id, "status": tc_args.get("status", "pass"),
                    "cycles_used": cycle + 1, "summary": tc_args.get("findings", "")[:300],
                    "vision_calls_used": vision_calls_used,
                }))
                return {
                    "agent_id": agent_id, "status": tc_args.get("status", "pass"),
                    "findings": tc_args.get("findings", ""),
                    "cycles_used": cycle + 1, "pages_consulted": pages_consulted,
                    "vision_calls_used": vision_calls_used,
                }
            else:
                tool_result = json.dumps({"error": f"Unknown tool: {tc_name}"})

            if tc_name != "finish":
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": tool_result if isinstance(tool_result, str) else json.dumps(tool_result),
                })

        # If this was the last cycle and model didn't call finish, force-return accumulated findings
        if cycle == max_cycles - 1 and not finished:
            force_findings = "\n".join(accumulated_findings) if accumulated_findings else "No data found within cycle limit."
            events.append(sse_event("sub_agent_done", {
                "agent_id": agent_id, "status": "pass" if accumulated_findings else "fail",
                "cycles_used": max_cycles, "summary": force_findings[:300],
                "vision_calls_used": vision_calls_used,
            }))
            return {
                "agent_id": agent_id,
                "status": "pass" if accumulated_findings else "fail",
                "findings": force_findings,
                "cycles_used": max_cycles, "pages_consulted": pages_consulted,
                "vision_calls_used": vision_calls_used,
            }

    # Fallback (shouldn't reach here due to force-finish above)
    events.append(sse_event("sub_agent_done", {
        "agent_id": agent_id, "status": "fail",
        "cycles_used": max_cycles, "summary": "Hit max cycles",
        "vision_calls_used": vision_calls_used,
    }))
    return {
        "agent_id": agent_id, "status": "fail",
        "findings": "Hit max cycles without finishing",
        "cycles_used": max_cycles, "pages_consulted": pages_consulted,
        "vision_calls_used": vision_calls_used,
    }


# ── Main Agent Loop ──

async def run_main_agent(
    conv_id: str,
    user_message: str,
    toggles: dict,
    history: list,
) -> AsyncGenerator[str, None]:
    """SSE generator. Main agent runs up to MAX_MAIN_CYCLES cycles with direct RAG tools.
    Tracks token usage. Streams final answer token-by-token."""

    client = _get_client()
    tracker = TokenTracker()
    vision_calls_used = 0

    # Extract @SYMBOL mentions from user message
    symbols = re.findall(r'@([A-Za-z]+)', user_message)
    symbols = [s.upper() for s in symbols]
    if symbols:
        symbols_note = f"User mentioned symbols: {', '.join(symbols)}"
    else:
        symbols_note = "No @symbols mentioned. Infer the NSE symbol from the company name if needed."

    # Build index summary
    index_summary = annual_report_service.get_index_summary(conv_id)
    system_prompt = MAIN_AGENT_PROMPT.replace("{index_summary}", index_summary)
    system_prompt = system_prompt.replace("{symbols_note}", symbols_note)

    # Build context from other toggles (web search, announcements)
    extra_context = ""
    if toggles.get("web_search"):
        from routers.chat import _fetch_web_context
        web = _fetch_web_context(user_message)
        if web:
            extra_context += f"\n\n## Web Search Results\n{web}"
    if toggles.get("announcements"):
        from routers.chat import _fetch_announcements_context
        ann = _fetch_announcements_context(user_message)
        if ann:
            extra_context += f"\n\n## News & Announcements\n{ann}"

    if extra_context:
        system_prompt += f"\n\n## Additional Context{extra_context}"

    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history
    for msg in history:
        if msg["role"] in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    # Collect sub-agent events (populated by threads)
    sub_agent_events = []

    for cycle in range(config.MAX_MAIN_CYCLES):
        yield sse_event("thinking", {
            "cycle": cycle + 1,
            "max": config.MAX_MAIN_CYCLES,
            "tokens": tracker.to_dict(),
        })

        # Flush any pending sub-agent events
        while sub_agent_events:
            yield sub_agent_events.pop(0)

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=config.MAIN_AGENT_MODEL,
                    messages=messages,
                    tools=MAIN_AGENT_TOOLS,
                ),
            )
            tracker.add_main(response.usage)
        except Exception as e:
            yield sse_event("error", {"message": f"Agent error: {str(e)}"})
            yield sse_event("answer", {"content": f"I encountered an error: {str(e)}", "done": True})
            yield sse_event("usage", tracker.to_dict())
            return

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)

            for tc in msg.tool_calls:
                yield sse_event("tool_call", {
                    "tool": tc.function.name,
                    "args": tc.function.arguments,
                })

                result, vision_calls_used = await _execute_main_tool(
                    tc, conv_id, sub_agent_events, tracker, vision_calls_used
                )

                # Flush sub-agent events
                while sub_agent_events:
                    yield sub_agent_events.pop(0)

                # Summarize for SSE
                summary = _summarize_result(tc.function.name, result)
                yield sse_event("tool_result", {
                    "tool": tc.function.name,
                    "summary": summary,
                })

                # Truncated result for LLM context (async to avoid blocking event loop)
                truncated = await _truncate_tool_result_async(tc.function.name, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": truncated,
                })

                # If finish tool was called, stream the answer
                if tc.function.name == "finish":
                    answer = result.get("answer", "")
                    for chunk in _chunk_text(answer):
                        yield sse_event("stream", {"token": chunk})
                    yield sse_event("answer", {"content": answer, "done": True})
                    yield sse_event("usage", tracker.to_dict())
                    return

        elif msg.content:
            # No tool calls, just text — stream it
            answer = msg.content
            for chunk in _chunk_text(answer):
                yield sse_event("stream", {"token": chunk})
            yield sse_event("answer", {"content": answer, "done": True})
            yield sse_event("usage", tracker.to_dict())
            return

    # Hit max cycles — force final answer
    messages.append({
        "role": "user",
        "content": "You've used all cycles. Provide your best answer now with what you have.",
    })
    try:
        loop = asyncio.get_event_loop()
        stream = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=config.MAIN_AGENT_MODEL,
                messages=messages,
                stream=True,
            ),
        )
        full_answer = ""
        def _read_stream():
            chunks = []
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    chunks.append(delta.content)
                if hasattr(chunk, 'usage') and chunk.usage:
                    tracker.add_main(chunk.usage)
            return chunks
        stream_chunks = await loop.run_in_executor(None, _read_stream)
        for token in stream_chunks:
            full_answer += token
            yield sse_event("stream", {"token": token})
        yield sse_event("answer", {"content": full_answer, "done": True})
    except Exception as e:
        yield sse_event("answer", {"content": f"I ran out of cycles and hit an error: {str(e)}", "done": True})

    yield sse_event("usage", tracker.to_dict())


def _chunk_text(text: str, chunk_size: int = 4) -> list[str]:
    """Split text into word-sized chunks for streaming effect."""
    words = text.split(' ')
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = ' '.join(words[i:i + chunk_size])
        if i > 0:
            chunk = ' ' + chunk
        chunks.append(chunk)
    return chunks


def _summarize_result(tool_name: str, result: dict) -> str:
    """Create a short summary of a tool result for the SSE feed."""
    if tool_name == "fetch_report_list":
        count = result.get("count", 0)
        if count > 0:
            reports = result.get("reports", [])
            years = [f"FY{r['from_yr']}-{r['to_yr']}" for r in reports[:3]]
            return f"Found {count} reports ({', '.join(years)}{'...' if count > 3 else ''})"
        return "No reports found"

    elif tool_name == "index_report":
        if result.get("error"):
            return f"Error: {result['error']}"
        pages = result.get("pages_indexed", 0)
        sections = len(result.get("key_sections", []))
        existed = result.get("already_existed", False)
        if existed:
            return f"Already indexed ({pages} pages, {sections} sections)"
        return f"Indexed {pages} pages, found {sections} sections"

    elif tool_name == "get_index_summary":
        return result.get("summary", "")[:200]

    elif tool_name == "get_pdf_index":
        sections = result.get("key_sections", [])
        return f"TOC with {len(sections)} sections"

    elif tool_name == "rag_search":
        count = result.get("count", 0)
        if count > 0:
            pages = [str(r["page_num"]) for r in result.get("results", [])[:5]]
            return f"Found {count} pages: [{', '.join(pages)}]"
        return "No matching pages found"

    elif tool_name == "get_page":
        if result.get("error"):
            return f"Error: {result['error']}"
        pn = result.get("page_num", "?")
        sf = result.get("source_file", "?")
        chars = len(result.get("text", ""))
        return f"Page {pn} from {sf} ({chars} chars)"

    elif tool_name == "read_page_as_image":
        if result.get("error"):
            return f"Vision error: {result['error']}"
        return f"Vision analysis of page {result.get('page_num', '?')}"

    elif tool_name == "spawn_sub_agents":
        results = result.get("sub_agent_results", [])
        passed = sum(1 for r in results if r.get("status") == "pass")
        vision = sum(r.get("vision_calls_used", 0) for r in results)
        summary = f"{passed}/{len(results)} agents succeeded"
        if vision > 0:
            summary += f", {vision} vision calls used"
        return summary

    elif tool_name == "create_note":
        return f"Saved note: {result.get('title', '')}"

    elif tool_name == "finish":
        return "Final answer delivered"

    return str(result)[:200]
