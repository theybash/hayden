'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navLinks = [
  { href: '/news', label: 'News Feed' },
  { href: '/portfolio', label: 'Portfolio' },
  { href: '/command-center', label: 'Command Center' },
];

export default function TopNav() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 z-50 bg-white border-b border-gray-100">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <Link href="/news" className="flex items-center gap-2">
          <span className="text-xl font-bold tracking-tight text-gray-900">
            HAYDEN
          </span>
          <span className="text-xs font-medium text-green-600 bg-green-50 px-1.5 py-0.5 rounded">
            BETA
          </span>
        </Link>

        {/* Nav Links */}
        <div className="hidden md:flex items-center gap-1">
          {navLinks.map((link) => {
            const isActive =
              pathname === link.href || pathname?.startsWith(link.href + '/');
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors relative ${
                  isActive
                    ? 'text-green-700 bg-green-50'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                {link.label}
                {isActive && (
                  <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-6 h-0.5 bg-green-600 rounded-full" />
                )}
              </Link>
            );
          })}
        </div>

        {/* Search Bar */}
        <div className="hidden lg:flex items-center">
          <div className="relative">
            <svg
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
            <input
              type="text"
              placeholder="Search news, stocks..."
              className="w-64 pl-10 pr-4 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500/20 focus:border-green-400 placeholder-gray-400 transition-all"
            />
          </div>
        </div>

        {/* Mobile menu button */}
        <div className="flex md:hidden">
          <button className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-50 rounded-lg">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        </div>
      </div>
    </nav>
  );
}
