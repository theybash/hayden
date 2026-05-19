export default function PortfolioPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-16">
      <div className="text-center">
        {/* Icon */}
        <div className="inline-flex items-center justify-center w-20 h-20 bg-green-50 rounded-2xl mb-6">
          <svg
            className="w-10 h-10 text-green-600"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M3 3v18h18M7 16l4-4 4 4 4-8"
            />
          </svg>
        </div>

        <h1 className="text-2xl font-bold text-gray-900 mb-2">Portfolio</h1>
        <p className="text-gray-500 mb-10 max-w-md mx-auto">
          Track your stocks and mutual funds. Get personalized news and alerts
          for your holdings.
        </p>

        {/* Mockup cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-lg mx-auto">
          {/* Card 1: Holdings overview */}
          <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm text-left">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">
                Total Value
              </span>
              <span className="w-2 h-2 bg-green-400 rounded-full" />
            </div>
            <div className="h-6 w-32 bg-gray-100 rounded mb-1" />
            <div className="h-4 w-20 bg-green-50 rounded" />
          </div>

          {/* Card 2: Top Gainer */}
          <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm text-left">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">
                Top Gainer
              </span>
              <span className="text-xs text-green-600 font-medium">
                +5.2%
              </span>
            </div>
            <div className="h-6 w-24 bg-gray-100 rounded mb-1" />
            <div className="h-4 w-16 bg-gray-50 rounded" />
          </div>

          {/* Card 3: Holdings list mockup */}
          <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm text-left sm:col-span-2">
            <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">
              Holdings
            </span>
            <div className="mt-3 space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 bg-gray-50 rounded-lg" />
                    <div>
                      <div className="h-4 w-20 bg-gray-100 rounded mb-1" />
                      <div className="h-3 w-14 bg-gray-50 rounded" />
                    </div>
                  </div>
                  <div className="h-4 w-16 bg-gray-50 rounded" />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Coming soon badge */}
        <div className="mt-10 inline-flex items-center gap-2 px-4 py-2 bg-gray-50 text-gray-500 text-sm font-medium rounded-full">
          <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse" />
          Coming Soon
        </div>
      </div>
    </div>
  );
}
