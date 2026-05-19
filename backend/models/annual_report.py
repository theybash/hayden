from pydantic import BaseModel
from typing import Optional, List


class ReportInfo(BaseModel):
    company_name: str
    symbol: str
    from_yr: str
    to_yr: str
    file_url: str
    file_type: str = "pdf"  # "pdf" or "zip"


class PageResult(BaseModel):
    page_num: int
    source_file: str
    fiscal_year: str
    text: str
    text_preview: str  # first ~200 chars
    score: float = 0.0


class SubAgentTask(BaseModel):
    query: str
    scope_years: List[str] = []
    scope_pages: List[int] = []
    objective: str  # one-line description of what to find


class SubAgentResult(BaseModel):
    task: SubAgentTask
    pages_consulted: List[int] = []
    cycles_used: int = 0
    status: str = "pending"  # "pass" | "fail" | "pending"
    findings: str = ""
    vision_calls_used: int = 0


class IndexedReportInfo(BaseModel):
    id: int
    conversation_id: str
    symbol: str
    company_name: Optional[str] = None
    from_yr: str
    to_yr: str
    file_url: Optional[str] = None
    page_count: int = 0
    pdf_path: Optional[str] = None  # kept on disk
    indexed_at: Optional[str] = None
