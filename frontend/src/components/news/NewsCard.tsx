'use client';

import { useState } from 'react';
import { NewsItem, getNewsSummary } from '@/lib/api';
import { timeAgo, getImpactColor, getImpactLabel } from '@/lib/utils';

interface NewsCardProps {
  item: NewsItem;
}

const CATEGORY_LABELS: Record<string, string> = {
  indian_markets: 'Indian Markets',
  global: 'Global',
  regulatory: 'Regulatory',
  macro: 'Macro',
  crypto: 'Crypto',
};

const CATEGORY_COLORS: Record<string, string> = {
  indian_markets: 'bg-green-50 text-green-700 border-green-100',
  global: 'bg-blue-50 text-blue-700 border-blue-100',
  regulatory: 'bg-purple-50 text-purple-700 border-purple-100',
  macro: 'bg-orange-50 text-orange-700 border-orange-100',
  crypto: 'bg-yellow-50 text-yellow-700 border-yellow-100',
};

export default function NewsCard({ item }: NewsCardProps) {
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [showSummary, setShowSummary] = useState(false);
  const [showCluster, setShowCluster] = useState(false);
  const [chatTooltip, setChatTooltip] = useState(false);

  const handleSummary = async () => {
    if (showSummary && summary) {
      setShowSummary(false);
      return;
    }
    if (summary) {
      setShowSummary(true);
      return;
    }
    setSummaryLoading(true);
    setShowSummary(true);
    try {
      const data = await getNewsSummary(item.id);
      setSummary(data.summary);
    } catch {
      setSummary('Failed to load summary. Please try again.');
    } finally {
      setSummaryLoading(false);
    }
  };

  const isCluster = item.source_count > 1;

  return (
    <article className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm hover:shadow-md transition-shadow">
      {/* Top row: Impact badge + category + source + timestamp */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2 flex-wrap">
          {/* Impact badge */}
          <span
            className={`inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold rounded-full border ${getImpactColor(
              item.impact_score
            )}`}
          >
            {item.impact_score}
            <span className="opacity-70"> {getImpactLabel(item.impact_score)}</span>
          </span>

          {/* Category tag */}
          {item.category && (
            <span
              className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-md border ${
                CATEGORY_COLORS[item.category] || 'bg-gray-50 text-gray-500 border-gray-100'
              }`}
            >
              {CATEGORY_LABELS[item.category] || item.category}
            </span>
          )}

          {/* Source name */}
          <span className="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-gray-50 text-gray-500 rounded-md border border-gray-100">
            {item.source_name || item.source}
          </span>

          {/* Symbol pill */}
          {item.symbol && (
            <span className="inline-flex items-center px-2 py-0.5 text-xs font-semibold bg-blue-50 text-blue-700 rounded-md border border-blue-100">
              {item.symbol}
            </span>
          )}

          {/* Cluster badge */}
          {isCluster && (
            <button
              onClick={() => setShowCluster(!showCluster)}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-indigo-50 text-indigo-700 rounded-md border border-indigo-100 hover:bg-indigo-100 transition-colors"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
                />
              </svg>
              {item.source_count} sources
            </button>
          )}
        </div>

        {/* Timestamp */}
        <span className="text-xs text-gray-400 whitespace-nowrap ml-3">
          {timeAgo(item.timestamp)}
        </span>
      </div>

      {/* Headline */}
      <h3 className="text-base font-semibold text-gray-900 leading-snug mb-2">
        {item.headline}
      </h3>

      {/* Cluster expansion — other sources */}
      {isCluster && showCluster && (
        <div className="mb-3 p-3 bg-indigo-50/50 border border-indigo-100 rounded-lg">
          <p className="text-xs font-medium text-indigo-700 mb-2">
            Also covered by:
          </p>
          <div className="space-y-1.5">
            {item.cluster_sources?.map((src, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="font-medium text-gray-700">{src}</span>
                {item.cluster_urls?.[i] && (
                  <a
                    href={item.cluster_urls[i]}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-indigo-600 hover:text-indigo-800"
                  >
                    View →
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* AI Summary (expandable) */}
      {showSummary && (
        <div className="mb-3 p-3 bg-green-50/50 border border-green-100 rounded-lg">
          {summaryLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <svg className="animate-spin h-4 w-4 text-green-600" viewBox="0 0 24 24">
                <circle
                  className="opacity-25"
                  cx="12" cy="12" r="10"
                  stroke="currentColor" strokeWidth="4" fill="none"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              Generating summary...
            </div>
          ) : (
            <p className="text-sm text-gray-700 leading-relaxed">{summary}</p>
          )}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-50">
        {/* AI Summary button */}
        <button
          onClick={handleSummary}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
            showSummary
              ? 'bg-green-100 text-green-700'
              : 'bg-gray-50 text-gray-600 hover:bg-green-50 hover:text-green-700'
          }`}
        >
          <span>✨</span> AI Summary
        </button>

        {/* Chat button */}
        <div className="relative">
          <button
            onMouseEnter={() => setChatTooltip(true)}
            onMouseLeave={() => setChatTooltip(false)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gray-50 text-gray-400 rounded-lg cursor-default"
          >
            <span>💬</span> Chat
          </button>
          {chatTooltip && (
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2.5 py-1 bg-gray-800 text-white text-xs rounded-md whitespace-nowrap">
              Coming soon
              <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-gray-800" />
            </div>
          )}
        </div>

        {/* Command Center button */}
        <button
          className="inline-flex items-center gap-1.5 px-2 py-1.5 text-xs text-gray-400 bg-gray-50 rounded-lg cursor-default"
          title="Open in Command Center (Coming soon)"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
            />
          </svg>
        </button>

        <div className="flex-1" />

        {/* View Original link */}
        {item.url && (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-green-600 hover:text-green-700 transition-colors"
          >
            View Original
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M14 5l7 7m0 0l-7 7m7-7H3"
              />
            </svg>
          </a>
        )}
      </div>
    </article>
  );
}
