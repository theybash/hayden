'use client';

interface ImpactFilterProps {
  value: number;
  onChange: (value: number) => void;
}

const segments = [
  { label: 'All', value: 0 },
  { label: 'Low', value: 0 },
  { label: 'Medium', value: 40 },
  { label: 'High', value: 70 },
];

export default function ImpactFilter({ value, onChange }: ImpactFilterProps) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-5 shadow-sm">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        {/* Left: Label and slider */}
        <div className="flex-1">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-gray-700">
              Impact Filter
            </span>
            <span className="text-sm text-gray-500">
              Showing impact{' '}
              <span className="font-semibold text-gray-900">{value}+</span>
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            className="w-full cursor-pointer"
          />
          <div className="flex justify-between mt-1">
            <span className="text-xs text-gray-400">0</span>
            <span className="text-xs text-gray-400">25</span>
            <span className="text-xs text-gray-400">50</span>
            <span className="text-xs text-gray-400">75</span>
            <span className="text-xs text-gray-400">100</span>
          </div>
        </div>

        {/* Right: Quick segment buttons */}
        <div className="flex gap-1.5 sm:ml-6">
          {segments.map((seg) => {
            const isActive = value === seg.value;
            return (
              <button
                key={seg.label}
                onClick={() => onChange(seg.value)}
                className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                  isActive
                    ? 'bg-green-600 text-white'
                    : 'bg-gray-50 text-gray-600 hover:bg-gray-100'
                }`}
              >
                {seg.label}
                {seg.label !== 'All' && (
                  <span className="ml-1 opacity-60">{seg.value}+</span>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
