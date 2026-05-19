const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3356';

export interface NewsItem {
  id: string;
  headline: string;
  source: string;
  source_name?: string;
  url?: string;
  symbol?: string;
  impact_score: number;
  relevance_score: number;
  category?: string;
  timestamp: string;
  summary?: string;
  description?: string;
  cluster_id?: string;
  source_count: number;
  cluster_headlines?: string[];
  cluster_urls?: string[];
  cluster_sources?: string[];
}

export interface PipelineStats {
  raw: number;
  nse: number;
  web: number;
  rss: number;
  after_dedup: number;
  after_relevance: number;
  after_scoring: number;
  after_clustering: number;
}

export async function fetchNews(
  minImpact: number = 0,
  category?: string,
  source?: string
): Promise<{ items: NewsItem[]; stats?: PipelineStats }> {
  const params = new URLSearchParams({ min_impact: String(minImpact) });
  if (category) params.set('category', category);
  if (source) params.set('source', source);

  const res = await fetch(`${API_BASE}/api/news?${params}`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch news: ${res.status}`);
  }
  const data = await res.json();
  return {
    items: Array.isArray(data) ? data : data.items ?? [],
    stats: data.stats,
  };
}

export async function refreshNews(): Promise<{ items: NewsItem[]; stats?: PipelineStats }> {
  const res = await fetch(`${API_BASE}/api/news/refresh`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error(`Failed to refresh news: ${res.status}`);
  }
  const data = await res.json();
  return {
    items: Array.isArray(data) ? data : data.items ?? [],
    stats: data.stats,
  };
}

export interface NewsletterSource {
  index: number;
  headline: string;
  url?: string;
  source_name?: string;
}

export interface NewsletterResult {
  text: string;
  word_count: number;
  generated_at: string;
  sources_count: number;
  sources: NewsletterSource[];
}

export async function generateNewsletter(mode: string = 'rundown', hours: number = 12): Promise<NewsletterResult> {
  const params = new URLSearchParams({ mode, hours: String(hours) });
  const res = await fetch(`${API_BASE}/api/news/newsletter?${params}`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error(`Failed to generate newsletter: ${res.status}`);
  }
  return res.json();
}

// ── Chat API ──

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  context_sources?: string;
  created_at?: string;
}

export interface ContextToggles {
  web_search: boolean;
  announcements: boolean;
  annual_reports: boolean;
}

export async function listConversations(): Promise<Conversation[]> {
  const res = await fetch(`${API_BASE}/api/chat/conversations`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to list conversations: ${res.status}`);
  return res.json();
}

export async function createConversation(): Promise<Conversation> {
  const res = await fetch(`${API_BASE}/api/chat/conversations`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed to create conversation: ${res.status}`);
  return res.json();
}

export async function deleteConversation(convId: string): Promise<void> {
  await fetch(`${API_BASE}/api/chat/conversations/${convId}`, { method: 'DELETE' });
}

export async function getMessages(convId: string): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/${convId}/messages`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to get messages: ${res.status}`);
  return res.json();
}

// Legacy non-streaming send (kept for backwards compat)
export async function sendMessage(
  convId: string,
  message: string,
  contextToggles: ContextToggles
): Promise<ChatMessage> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/${convId}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, context_toggles: contextToggles }),
  });
  if (!res.ok) throw new Error(`Failed to send message: ${res.status}`);
  return res.json();
}

// ── SSE Streaming API ──

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export async function sendMessageStream(
  convId: string,
  message: string,
  contextToggles: ContextToggles,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/${convId}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, context_toggles: contextToggles }),
    signal,
  });

  if (!res.ok) throw new Error(`Failed to send message: ${res.status}`);

  const contentType = res.headers.get('content-type') || '';

  // If backend returns JSON (old non-streaming response), handle it
  if (contentType.includes('application/json')) {
    const data = await res.json();
    const content = data.content || data.message || JSON.stringify(data);
    onEvent({ event: 'answer', data: { content, done: true } });
    return;
  }

  // SSE streaming response
  if (!res.body) throw new Error('No response body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse SSE events from buffer
    const lines = buffer.split('\n');
    buffer = lines.pop() || ''; // Keep incomplete line in buffer

    let currentEvent = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        const dataStr = line.slice(6).trim();
        try {
          const data = JSON.parse(dataStr);
          onEvent({ event: currentEvent || 'message', data });
        } catch {
          // Non-JSON data
          onEvent({ event: currentEvent || 'message', data: { content: dataStr } });
        }
        currentEvent = '';
      }
    }
  }
}

// ── Annual Reports API ──

export interface IndexedReport {
  id: number;
  conversation_id: string;
  symbol: string;
  company_name?: string;
  from_yr: string;
  to_yr: string;
  file_url?: string;
  page_count: number;
  pdf_path?: string;
  indexed_at?: string;
}

export interface ReportListItem {
  company_name: string;
  symbol: string;
  from_yr: string;
  to_yr: string;
  file_url: string;
  file_type: string;
}

export async function listIndexedReports(convId: string): Promise<IndexedReport[]> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/${convId}/reports`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to list reports: ${res.status}`);
  return res.json();
}

export async function fetchReportList(symbol: string): Promise<ReportListItem[]> {
  const res = await fetch(`${API_BASE}/api/chat/reports/${symbol}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch report list: ${res.status}`);
  return res.json();
}

export async function indexReport(
  convId: string,
  symbol: string,
  fromYr: string,
  toYr: string,
  fileUrl: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/${convId}/index-report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, from_yr: fromYr, to_yr: toYr, file_url: fileUrl }),
  });
  if (!res.ok) throw new Error(`Failed to index report: ${res.status}`);
  return res.json();
}

// ── Notes API ──

export interface Note {
  filename: string;
  title: string;
  preview: string;
}

export async function listNotes(convId: string): Promise<Note[]> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/${convId}/notes`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to list notes: ${res.status}`);
  return res.json();
}

export async function createNote(convId: string, title: string, content: string): Promise<Note> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/${convId}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content }),
  });
  if (!res.ok) throw new Error(`Failed to create note: ${res.status}`);
  return res.json();
}

export async function getNewsSummary(itemId: string): Promise<{ summary: string }> {
  const res = await fetch(`${API_BASE}/api/news/${itemId}/summary`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error(`Failed to get summary: ${res.status}`);
  }
  return res.json();
}
