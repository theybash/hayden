'use client';

import { useState, useEffect, useCallback } from 'react';
import { NewsItem, PipelineStats, fetchNews, refreshNews } from '@/lib/api';
import Newsletter from './Newsletter';
import ImpactFilter from './ImpactFilter';
import NewsCard from './NewsCard';

function SkeletonCard() {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm animate-pulse">
      <div className="flex items-center gap-2 mb-3">
        <div className="h-6 w-20 bg-gray-100 rounded-full" />
        <div className="h-5 w-16 bg-gray-100 rounded" />
        <div className="h-5 w-12 bg-gray-100 rounded" />
      </div>
      <div className="h-5 w-3/4 bg-gray-100 rounded mb-2" />
      <div className="h-4 w-1/2 bg-gray-50 rounded" />
      <div className="flex gap-2 mt-4 pt-3 border-t border-gray-50">
        <div className="h-7 w-24 bg-gray-50 rounded-lg" />
        <div className="h-7 w-16 bg-gray-50 rounded-lg" />
      </div>
    </div>
  );
}

const categoryFilters = [
  { label: 'All', value: undefined },
  { label: 'Indian Markets', value: 'indian_markets' },
  { label: 'Global', value: 'global' },
  { label: 'Regulatory', value: 'regulatory' },
  { label: 'Macro', value: 'macro' },
  { label: 'Crypto', value: 'crypto' },
] as const;

export default function NewsFeed() {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [stats, setStats] = useState<PipelineStats | undefined>();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minImpact, setMinImpact] = useState(0);
  const [category, setCategory] = useState<string | undefined>(undefined);

  const loadNews = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchNews(minImpact, category);
      setItems(data.items);
      if (data.stats) setStats(data.stats);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load news');
    } finally {
      setLoading(false);
    }
  }, [minImpact, category]);

  useEffect(() => {
    loadNews();
  }, [loadNews]);

  const handleRefresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const data = await refreshNews();
      setItems(data.items);
      if (data.stats) setStats(data.stats);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh news');
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Newsletter */}
      <Newsletter />

      {/* Filters */}
      <div className="flex flex-col gap-4">
        <ImpactFilter value={minImpact} onChange={setMinImpact} />

        {/* Category filter */}
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-sm font-medium text-gray-700">Category</span>
          <div className="flex gap-1.5 flex-wrap">
            {categoryFilters.map((cf) => {
              const isActive = category === cf.value;
              return (
                <button
                  key={cf.label}
                  onClick={() => setCategory(cf.value)}
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                    isActive
                      ? 'bg-green-600 text-white'
                      : 'bg-gray-50 text-gray-600 hover:bg-gray-100'
                  }`}
                >
                  {cf.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Header row with count, stats, and refresh */}
      <div className="flex items-center justify-between">
        <div className="text-sm text-gray-500">
          {loading ? (
            'Loading...'
          ) : (
            <div className="flex items-center gap-3">
              <span>
                <span className="font-semibold text-gray-900">{items.length}</span> news items
              </span>
              {stats && (
                <span className="text-xs text-gray-400">
                  ({stats.raw} fetched → {stats.after_dedup} unique → {stats.after_relevance} relevant → {stats.after_clustering} final)
                </span>
              )}
            </div>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
        >
          <svg
            className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          {refreshing ? 'Fetching fresh news...' : 'Refresh'}
        </button>
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-red-50 border border-red-100 rounded-xl p-4 text-sm text-red-700">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            {error}
          </div>
        </div>
      )}

      {/* Loading skeletons */}
      {loading && (
        <div className="space-y-4">
          {[...Array(5)].map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {/* News cards */}
      {!loading && items.length > 0 && (
        <div className="space-y-4">
          {items.map((item) => (
            <NewsCard key={item.id} item={item} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && items.length === 0 && (
        <div className="text-center py-16">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-gray-50 rounded-full mb-4">
            <svg className="w-8 h-8 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"
              />
            </svg>
          </div>
          <h3 className="text-base font-medium text-gray-900 mb-1">No news items</h3>
          <p className="text-sm text-gray-500 mb-4">
            {minImpact > 0
              ? `No items with impact score ${minImpact}+. Try lowering the filter.`
              : 'No news items available. Try refreshing.'}
          </p>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50"
          >
            Refresh News
          </button>
        </div>
      )}
    </div>
  );
}
