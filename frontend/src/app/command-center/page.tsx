'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Conversation,
  ChatMessage,
  ContextToggles,
  SSEEvent,
  IndexedReport,
  listConversations,
  createConversation,
  deleteConversation,
  getMessages,
  sendMessageStream,
  listIndexedReports,
} from '@/lib/api';

const DEFAULT_TOGGLES: ContextToggles = {
  web_search: true,
  announcements: true,
  annual_reports: false,
};

const TOGGLE_OPTIONS = [
  { key: 'web_search' as const, label: 'Web Search', icon: '🌐' },
  { key: 'announcements' as const, label: 'News & Filings', icon: '📰' },
  { key: 'annual_reports' as const, label: 'Annual Reports', icon: '📊' },
] as const;

// ── Agent Activity Types ──
interface AgentStep {
  id: number;
  type: 'thinking' | 'tool_call' | 'tool_result' | 'sub_agents_spawned' | 'sub_agent_progress' | 'sub_agent_done' | 'error';
  data: Record<string, unknown>;
  timestamp: number;
}

interface TokenUsage {
  main_agent: { model: string; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number };
  sub_agents: { model: string; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number };
  total_tokens: number;
}

export default function CommandCenterPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [toggles, setToggles] = useState<ContextToggles>(DEFAULT_TOGGLES);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [agentSteps, setAgentSteps] = useState<AgentStep[]>([]);
  const [streamingContent, setStreamingContent] = useState('');
  const [agentExpanded, setAgentExpanded] = useState(true);
  const [indexedReports, setIndexedReports] = useState<IndexedReport[]>([]);
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stepIdRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // Load conversations and auto-select the most recent one
  useEffect(() => {
    listConversations().then((convs) => {
      setConversations(convs);
      if (!activeConvId && convs.length > 0) {
        setActiveConvId(convs[0].id); // most recent (sorted by updated_at desc)
      }
    }).catch(console.error);
  }, []);

  // Load messages + indexed reports when active conversation changes
  useEffect(() => {
    if (activeConvId) {
      getMessages(activeConvId).then(setMessages).catch(console.error);
      listIndexedReports(activeConvId).then(setIndexedReports).catch(() => setIndexedReports([]));
    } else {
      setMessages([]);
      setIndexedReports([]);
    }
  }, [activeConvId]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent, agentSteps]);

  const handleNewChat = async () => {
    try {
      const conv = await createConversation();
      setConversations((prev) => [conv, ...prev]);
      setActiveConvId(conv.id);
      setMessages([]);
      setAgentSteps([]);
      setStreamingContent('');
      setTokenUsage(null);
      inputRef.current?.focus();
    } catch (err) {
      console.error(err);
    }
  };

  const handleDeleteConv = async (convId: string) => {
    try {
      await deleteConversation(convId);
      setConversations((prev) => prev.filter((c) => c.id !== convId));
      if (activeConvId === convId) {
        setActiveConvId(null);
        setMessages([]);
        setAgentSteps([]);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleStop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setSending(false);
    // If we have streaming content, commit it as a message
    setStreamingContent((prev) => {
      if (prev) {
        setMessages((msgs) => [...msgs, { role: 'assistant', content: prev + '\n\n_(stopped)_' }]);
      }
      return '';
    });
  }, []);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;

    let convId = activeConvId;

    // Auto-create conversation if none active
    if (!convId) {
      try {
        const conv = await createConversation();
        setConversations((prev) => [conv, ...prev]);
        convId = conv.id;
        setActiveConvId(conv.id);
      } catch {
        return;
      }
    }

    // Setup abort controller
    const abort = new AbortController();
    abortRef.current = abort;

    setInput('');
    setSending(true);
    setAgentSteps([]);
    setStreamingContent('');
    setAgentExpanded(true);
    setTokenUsage(null);

    // Optimistically add user message
    const userMsg: ChatMessage = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);

    try {
      await sendMessageStream(convId, text, toggles, (event: SSEEvent) => {
        // Check if aborted
        if (abort.signal.aborted) return;

        const { event: evtType, data } = event;

        if (evtType === 'thinking' || evtType === 'tool_call' || evtType === 'tool_result' ||
            evtType === 'sub_agents_spawned' || evtType === 'sub_agent_progress' ||
            evtType === 'sub_agent_done' || evtType === 'error') {
          setAgentSteps((prev) => [...prev, {
            id: stepIdRef.current++,
            type: evtType as AgentStep['type'],
            data,
            timestamp: Date.now(),
          }]);
          // Update live token count from thinking events
          if (evtType === 'thinking' && data.tokens) {
            setTokenUsage(data.tokens as unknown as TokenUsage);
          }
        } else if (evtType === 'stream') {
          // Token-by-token streaming
          setStreamingContent((prev) => prev + ((data.token as string) || ''));
        } else if (evtType === 'answer') {
          const content = (data.content as string) || '';
          setStreamingContent('');
          setMessages((prev) => [...prev, { role: 'assistant', content }]);
          setAgentExpanded(false);
        } else if (evtType === 'usage') {
          // Final token usage
          setTokenUsage(data as unknown as TokenUsage);
        } else if (evtType === 'done') {
          // Stream complete
        }
      }, abort.signal);

      // Refresh conversation list (title may have been auto-generated)
      listConversations().then(setConversations).catch(() => {});
      if (convId) {
        listIndexedReports(convId).then(setIndexedReports).catch(() => {});
      }
    } catch (err) {
      if (abort.signal.aborted) return; // User stopped, don't show error
      setStreamingContent('');
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${err instanceof Error ? err.message : 'Failed to get response'}` },
      ]);
    } finally {
      setSending(false);
      abortRef.current = null;
      inputRef.current?.focus();
    }
  }, [input, sending, activeConvId, toggles]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex h-[calc(100vh-64px)]">
      {/* Sidebar */}
      {sidebarOpen && (
        <div className="w-72 border-r border-gray-200 bg-white flex flex-col">
          <div className="p-4 border-b border-gray-100">
            <button
              onClick={handleNewChat}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              New Chat
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-1">
            {conversations.length === 0 && (
              <p className="text-xs text-gray-400 text-center py-8">No conversations yet</p>
            )}
            {conversations.map((conv) => (
              <div
                key={conv.id}
                className={`group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-colors ${
                  activeConvId === conv.id
                    ? 'bg-green-50 text-green-800'
                    : 'text-gray-600 hover:bg-gray-50'
                }`}
                onClick={() => setActiveConvId(conv.id)}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{conv.title}</div>
                  <div className="text-xs text-gray-400">
                    {new Date(conv.updated_at).toLocaleDateString('en-IN', {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDeleteConv(conv.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>

          {/* Indexed Reports Panel */}
          {indexedReports.length > 0 && (
            <div className="border-t border-gray-100 p-3">
              <div className="text-xs font-medium text-gray-500 mb-2">Indexed Reports</div>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {indexedReports.map((r) => (
                  <div key={r.id} className="text-xs text-gray-600 bg-gray-50 rounded px-2 py-1.5">
                    <span className="font-medium">{r.symbol}</span> FY{r.from_yr}-{r.to_yr}
                    <span className="text-gray-400 ml-1">({r.page_count}p)</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Main chat area */}
      <div className="flex-1 flex flex-col bg-gray-50">
        {/* Top bar */}
        <div className="border-b border-gray-200 bg-white px-4 py-3 flex items-center gap-3">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-1.5 text-gray-500 hover:text-gray-700 rounded-md hover:bg-gray-100"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>

          <div className="h-5 w-px bg-gray-200" />

          {/* Context toggles */}
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-gray-400 mr-1">Context:</span>
            {TOGGLE_OPTIONS.map((opt) => {
              const active = toggles[opt.key];
              return (
                <button
                  key={opt.key}
                  onClick={() => setToggles((prev) => ({ ...prev, [opt.key]: !prev[opt.key] }))}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                    active
                      ? 'bg-green-100 text-green-700 border border-green-200'
                      : 'bg-gray-50 text-gray-500 border border-gray-200 hover:bg-gray-100'
                  }`}
                  title={`Toggle ${opt.label}`}
                >
                  <span>{opt.icon}</span>
                  {opt.label}
                </button>
              );
            })}
          </div>

          {/* Token usage display */}
          {tokenUsage && tokenUsage.total_tokens > 0 && (
            <>
              <div className="h-5 w-px bg-gray-200 ml-auto" />
              <div className="flex items-center gap-2 text-[10px] text-gray-400">
                <span title={`${tokenUsage.main_agent.model}: ${tokenUsage.main_agent.total_tokens} tokens (${tokenUsage.main_agent.calls} calls)`}>
                  {tokenUsage.main_agent.model}: {(tokenUsage.main_agent.total_tokens / 1000).toFixed(1)}k
                </span>
                {tokenUsage.sub_agents.calls > 0 && (
                  <span title={`${tokenUsage.sub_agents.model}: ${tokenUsage.sub_agents.total_tokens} tokens (${tokenUsage.sub_agents.calls} calls)`}>
                    {tokenUsage.sub_agents.model}: {(tokenUsage.sub_agents.total_tokens / 1000).toFixed(1)}k
                  </span>
                )}
                <span className="font-medium text-gray-500">
                  = {(tokenUsage.total_tokens / 1000).toFixed(1)}k
                </span>
              </div>
            </>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-6">
          {messages.length === 0 && !sending && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center max-w-md">
                <div className="w-16 h-16 bg-green-50 rounded-2xl mx-auto mb-4 flex items-center justify-center">
                  <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.5}
                      d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
                    />
                  </svg>
                </div>
                <h2 className="text-lg font-semibold text-gray-900 mb-1">Research Command Center</h2>
                <p className="text-sm text-gray-500 mb-4">
                  Ask about any stock, sector, or market event. Toggle context sources above to control what data I pull in.
                  {toggles.annual_reports && (
                    <span className="block mt-2 text-green-600 font-medium">
                      Annual Reports mode is ON — I&apos;ll search through indexed annual reports to answer your questions.
                    </span>
                  )}
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  {(toggles.annual_reports
                    ? ['What was @RELIANCE revenue in FY2024?', 'Compare @TCS and @INFY margins', 'Analyze @HDFCBANK asset quality']
                    : ['What happened to RELIANCE today?', 'Analyze IT sector outlook', 'Why is crude spiking?']
                  ).map((q) => (
                    <button
                      key={q}
                      onClick={() => {
                        setInput(q);
                        inputRef.current?.focus();
                      }}
                      className="px-3 py-1.5 text-xs bg-white border border-gray-200 text-gray-600 rounded-lg hover:bg-green-50 hover:border-green-200 hover:text-green-700 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="max-w-3xl mx-auto space-y-4">
            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                    msg.role === 'user'
                      ? 'bg-green-600 text-white'
                      : 'bg-white border border-gray-100 text-gray-800 shadow-sm'
                  }`}
                >
                  <div className={`text-sm leading-relaxed whitespace-pre-wrap ${
                    msg.role === 'assistant' ? 'prose prose-sm prose-gray max-w-none' : ''
                  }`}>
                    {msg.content}
                  </div>
                </div>
              </div>
            ))}

            {/* Agent Activity Feed */}
            {agentSteps.length > 0 && (
              <div className="bg-white border border-gray-100 rounded-2xl shadow-sm overflow-hidden">
                <button
                  onClick={() => setAgentExpanded(!agentExpanded)}
                  className="w-full flex items-center gap-2 px-4 py-2.5 text-xs font-medium text-gray-500 hover:bg-gray-50 transition-colors"
                >
                  <svg className={`w-3 h-3 transition-transform ${agentExpanded ? 'rotate-90' : ''}`} fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" />
                  </svg>
                  {sending && (
                    <svg className="w-3.5 h-3.5 animate-spin text-green-600" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  )}
                  {sending ? `Agent working... (${agentSteps.length} steps)` : `Research completed (${agentSteps.length} steps)`}
                </button>

                {agentExpanded && (
                  <div className="border-t border-gray-50 px-4 py-2 space-y-1.5 max-h-64 overflow-y-auto">
                    {agentSteps.map((step) => (
                      <AgentStepRow key={step.id} step={step} />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Streaming content (token by token) */}
            {streamingContent && (
              <div className="flex justify-start">
                <div className="max-w-[85%] bg-white border border-gray-100 rounded-2xl px-4 py-3 shadow-sm">
                  <div className="text-sm leading-relaxed whitespace-pre-wrap prose prose-sm prose-gray max-w-none">
                    {streamingContent}
                    <span className="inline-block w-1.5 h-4 bg-green-500 animate-pulse ml-0.5" />
                  </div>
                </div>
              </div>
            )}

            {/* Simple thinking indicator (no agent steps yet, no streaming yet) */}
            {sending && agentSteps.length === 0 && !streamingContent && (
              <div className="flex justify-start">
                <div className="bg-white border border-gray-100 rounded-2xl px-4 py-3 shadow-sm">
                  <div className="flex items-center gap-2 text-sm text-gray-400">
                    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    Thinking...
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input */}
        <div className="border-t border-gray-200 bg-white p-4">
          <div className="max-w-3xl mx-auto flex items-end gap-3">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                toggles.annual_reports
                  ? 'Ask about annual reports — e.g. "What was @RELIANCE revenue in FY2024?"'
                  : 'Ask about a stock, sector, or market event...'
              }
              rows={1}
              disabled={sending}
              className="flex-1 resize-none px-4 py-2.5 text-sm border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-transparent disabled:bg-gray-50 disabled:text-gray-400"
              style={{ minHeight: '44px', maxHeight: '120px' }}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement;
                target.style.height = '44px';
                target.style.height = Math.min(target.scrollHeight, 120) + 'px';
              }}
            />
            {sending ? (
              <button
                onClick={handleStop}
                className="flex-shrink-0 w-10 h-10 bg-red-500 text-white rounded-xl flex items-center justify-center hover:bg-red-600 transition-colors"
                title="Stop"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim()}
                className="flex-shrink-0 w-10 h-10 bg-green-600 text-white rounded-xl flex items-center justify-center hover:bg-green-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


// ── Agent Step Row Component ──

function AgentStepRow({ step }: { step: AgentStep }) {
  const { type, data } = step;

  const iconClass = "w-3 h-3 flex-shrink-0";

  if (type === 'thinking') {
    return (
      <div className="flex items-center gap-2 text-xs text-gray-400">
        <svg className={`${iconClass} text-blue-400`} fill="currentColor" viewBox="0 0 20 20">
          <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm1 11H9v-2h2v2zm0-4H9V5h2v4z" />
        </svg>
        <span>Cycle {String(data.cycle)}/{String(data.max)}</span>
        {data.tokens && (data.tokens as Record<string, unknown>).total_tokens ? (
          <span className="text-gray-300 ml-1">
            ({((data.tokens as Record<string, unknown>).total_tokens as number / 1000).toFixed(1)}k tokens used)
          </span>
        ) : null}
      </div>
    );
  }

  if (type === 'tool_call') {
    const toolName = String(data.tool || '').replace(/_/g, ' ');
    let detail = '';
    try {
      const args = typeof data.args === 'string' ? JSON.parse(data.args as string) : data.args;
      if (args?.symbol) detail = args.symbol;
      else if (args?.query) detail = `"${String(args.query).slice(0, 50)}"`;
      else if (args?.tasks) detail = `${(args.tasks as unknown[]).length} tasks`;
    } catch { /* ignore */ }

    return (
      <div className="flex items-center gap-2 text-xs text-gray-600">
        <svg className={`${iconClass} text-amber-500`} fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd" />
        </svg>
        <span className="font-medium">{toolName}</span>
        {detail && <span className="text-gray-400">{detail}</span>}
      </div>
    );
  }

  if (type === 'tool_result') {
    return (
      <div className="flex items-center gap-2 text-xs text-gray-500 pl-5">
        <svg className={`${iconClass} text-green-500`} fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
        </svg>
        {String(data.summary || '')}
      </div>
    );
  }

  if (type === 'sub_agents_spawned') {
    const tasks = (data.tasks as string[]) || [];
    return (
      <div className="text-xs text-purple-600 pl-1">
        <div className="flex items-center gap-2 font-medium">
          <svg className={`${iconClass}`} fill="currentColor" viewBox="0 0 20 20">
            <path d="M13 6a3 3 0 11-6 0 3 3 0 016 0zM18 8a2 2 0 11-4 0 2 2 0 014 0zM14 15a4 4 0 00-8 0v1h8v-1zM6 8a2 2 0 11-4 0 2 2 0 014 0zM16 18v-1a5.972 5.972 0 00-.75-2.906A3.005 3.005 0 0119 17v1h-3zM4.75 14.094A5.973 5.973 0 004 17v1H1v-1a3 3 0 013.75-2.906z" />
          </svg>
          Spawned {String(data.count)} sub-agents
        </div>
        {tasks.map((t, i) => (
          <div key={i} className="text-gray-500 pl-5 mt-0.5">• {t}</div>
        ))}
      </div>
    );
  }

  if (type === 'sub_agent_progress') {
    const status = String(data.status || 'working');
    const visionInfo = data.vision_calls_used ? ` (vision: ${data.vision_calls_used}/${Number(data.vision_calls_used) + Number(data.vision_calls_remaining || 0)})` : '';
    return (
      <div className="flex items-center gap-2 text-xs text-gray-400 pl-5">
        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
        <span>Agent #{String(data.agent_id)}: {status}</span>
        {data.query ? <span className="text-gray-300 truncate max-w-[200px]">&quot;{String(data.query).slice(0, 40)}&quot;</span> : null}
        {data.page ? <span className="text-gray-300">p.{String(data.page)}</span> : null}
        {data.objective ? <span className="text-gray-300 truncate max-w-[200px]">{String(data.objective).slice(0, 40)}</span> : null}
        {visionInfo ? <span className="text-amber-500">{visionInfo}</span> : null}
      </div>
    );
  }

  if (type === 'sub_agent_done') {
    const passed = data.status === 'pass';
    const visionInfo = data.vision_calls_used ? ` | ${data.vision_calls_used} vision` : '';
    return (
      <div className={`flex items-start gap-2 text-xs pl-5 ${passed ? 'text-green-600' : 'text-red-500'}`}>
        <span className={`mt-0.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${passed ? 'bg-green-500' : 'bg-red-400'}`} />
        <div>
          <span className="font-medium">Agent #{String(data.agent_id)} {passed ? 'found data' : 'no data'}</span>
          <span className="text-gray-400"> ({String(data.cycles_used)} cycles{visionInfo})</span>
          {data.summary ? <div className="text-gray-500 mt-0.5">{String(data.summary).slice(0, 200)}</div> : null}
        </div>
      </div>
    );
  }

  if (type === 'error') {
    return (
      <div className="flex items-center gap-2 text-xs text-red-500">
        <svg className={`${iconClass}`} fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
        </svg>
        {String(data.message || 'Unknown error')}
      </div>
    );
  }

  return null;
}
