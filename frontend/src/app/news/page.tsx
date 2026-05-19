import NewsFeed from '@/components/news/NewsFeed';

export default function NewsPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">News Feed</h1>
        <p className="text-sm text-gray-500 mt-1">
          AI-scored financial news from NSE filings and web sources
        </p>
      </div>
      <NewsFeed />
    </div>
  );
}
