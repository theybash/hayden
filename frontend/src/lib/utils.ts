export function timeAgo(date: string | Date): string {
  const now = new Date();
  const then = new Date(date);
  const seconds = Math.floor((now.getTime() - then.getTime()) / 1000);

  if (seconds < 60) return 'just now';
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60);
    return `${mins}m ago`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours}h ago`;
  }
  if (seconds < 604800) {
    const days = Math.floor(seconds / 86400);
    return `${days}d ago`;
  }
  return then.toLocaleDateString('en-IN', {
    month: 'short',
    day: 'numeric',
  });
}

export function getImpactColor(score: number): string {
  if (score >= 80) return 'bg-red-100 text-red-700 border-red-200';
  if (score >= 61) return 'bg-orange-100 text-orange-700 border-orange-200';
  if (score >= 31) return 'bg-amber-100 text-amber-700 border-amber-200';
  return 'bg-gray-100 text-gray-600 border-gray-200';
}

export function getImpactBgColor(score: number): string {
  if (score >= 80) return 'bg-red-500';
  if (score >= 61) return 'bg-orange-500';
  if (score >= 31) return 'bg-amber-500';
  return 'bg-gray-400';
}

export function getImpactLabel(score: number): string {
  if (score >= 80) return 'Critical';
  if (score >= 61) return 'High';
  if (score >= 31) return 'Medium';
  return 'Low';
}
