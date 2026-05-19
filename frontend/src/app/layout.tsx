import type { Metadata } from 'next';
import TopNav from '@/components/TopNav';
import './globals.css';

export const metadata: Metadata = {
  title: 'Hayden - Smart Financial Intelligence',
  description: 'AI-powered financial news and research platform',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased bg-white text-gray-900">
        <TopNav />
        <main className="min-h-[calc(100vh-4rem)]">{children}</main>
      </body>
    </html>
  );
}
