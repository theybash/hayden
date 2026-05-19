'use client';

import { useState, useMemo } from 'react';
import { NewsletterResult, NewsletterSource, generateNewsletter } from '@/lib/api';

function CitedText({ text, sources }: { text: string; sources: NewsletterSource[] }) {
  const sourceMap = useMemo(() => {
    const map: Record<number, NewsletterSource> = {};
    for (const s of sources) map[s.index] = s;
    return map;
  }, [sources]);

  // Split text on citation patterns like [1], [2][3], [1][2]
  const parts = text.split(/(\[\d+\])/g);

  return (
    <>
      {parts.map((part, i) => {
        const match = part.match(/^\[(\d+)\]$/);
        if (match) {
          const idx = parseInt(match[1]);
          const src = sourceMap[idx];
          if (src?.url) {
            return (
              <a
                key={i}
                href={src.url}
                target="_blank"
                rel="noopener noreferrer"
                title={`${src.source_name}: ${src.headline}`}
                className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 text-[10px] font-bold bg-green-100 text-green-700 rounded hover:bg-green-200 transition-colors no-underline align-super"
              >
                {idx}
              </a>
            );
          }
          return (
            <span key={i} className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 text-[10px] font-bold bg-gray-100 text-gray-500 rounded align-super">
              {idx}
            </span>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

const modes = [
  { key: 'glance', label: 'Quick Glance' },
  { key: 'rundown', label: 'The Rundown' },
  { key: 'full', label: 'Full Brief' },
] as const;

const timePresets = [1, 3, 6, 12, 24, 48];

function formatHours(h: number): string {
  if (h < 24) return `${h}h`;
  return `${h / 24}d`;
}

export default function Newsletter() {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<string>('rundown');
  const [hours, setHours] = useState(12);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NewsletterResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await generateNewsletter(mode, hours);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate newsletter');
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = () => {
    if (!result) return;
    const blob = new Blob([result.text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `catch-me-up-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 rounded-xl overflow-hidden">
      {/* Header — always visible */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-green-50/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="text-lg">📰</span>
          <span className="font-semibold text-gray-900">Catch Me Up</span>
          <span className="text-xs text-gray-500">AI-generated timeline briefing</span>
        </div>
        <svg
          className={`w-5 h-5 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Collapsible body */}
      {open && (
        <div className="px-5 pb-5 space-y-4">
          {/* Briefing mode */}
          <div className="space-y-2">
            <span className="text-sm font-medium text-gray-700">Briefing depth</span>
            <div className="flex gap-2">
              {modes.map((m) => (
                <button
                  key={m.key}
                  onClick={() => setMode(m.key)}
                  className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                    mode === m.key
                      ? 'bg-green-600 text-white'
                      : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          {/* Time range */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700">Time range</span>
              <span className="text-xs font-medium text-green-700 bg-green-100 px-2 py-0.5 rounded-full">
                Last {hours < 24 ? `${hours} hour${hours !== 1 ? 's' : ''}` : `${hours / 24} day${hours > 24 ? 's' : ''}`}
              </span>
            </div>

            {/* Time preset buttons */}
            <div className="flex gap-1.5">
              {timePresets.map((h) => (
                <button
                  key={h}
                  onClick={() => setHours(h)}
                  className={`flex-1 px-2 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                    hours === h
                      ? 'bg-green-600 text-white'
                      : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  {formatHours(h)}
                </button>
              ))}
            </div>

            {/* Slider */}
            <input
              type="range"
              min={1}
              max={48}
              step={1}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
              className="w-full accent-green-600"
            />
            <div className="flex justify-between text-[10px] text-gray-400">
              <span>1h</span>
              <span>12h</span>
              <span>24h</span>
              <span>48h</span>
            </div>
          </div>

          {/* Generate button */}
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-semibold bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50"
          >
            {loading ? (
              <>
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                Generating briefing...
              </>
            ) : (
              'Catch Me Up'
            )}
          </button>

          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-100 rounded-lg p-3 text-sm text-red-700">{error}</div>
          )}

          {/* Result */}
          {result && result.word_count > 0 && (
            <div className="space-y-3">
              <div className="bg-white rounded-lg border border-gray-100 p-4">
                <div className="prose prose-sm prose-gray max-w-none whitespace-pre-wrap">
                  <CitedText text={result.text} sources={result.sources} />
                </div>
              </div>

              {/* Sources list */}
              {result.sources.length > 0 && (
                <details className="bg-gray-50 rounded-lg border border-gray-100">
                  <summary className="px-4 py-2.5 text-xs font-medium text-gray-600 cursor-pointer hover:text-gray-800">
                    Sources ({result.sources.length})
                  </summary>
                  <div className="px-4 pb-3 space-y-1">
                    {result.sources.map((s) => (
                      <div key={s.index} className="flex items-baseline gap-2 text-xs">
                        <span className="text-green-700 font-semibold flex-shrink-0">[{s.index}]</span>
                        <span className="text-gray-500">{s.source_name}</span>
                        {s.url ? (
                          <a
                            href={s.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-green-600 hover:text-green-700 truncate"
                          >
                            {s.headline}
                          </a>
                        ) : (
                          <span className="text-gray-700 truncate">{s.headline}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              )}

              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-400">
                  {result.word_count} words &middot; {result.sources_count} sources analyzed
                </span>
                <button
                  onClick={handleDownload}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                    />
                  </svg>
                  Download .txt
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
