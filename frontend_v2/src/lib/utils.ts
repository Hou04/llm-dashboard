/**
 * Shared formatting utilities for the dashboard.
 *
 * Extracted from individual page files to avoid duplication.
 * Every page that needs fmt/fmtK should import from here.
 */

/** Format a number to 2 decimal places (for USD values). */
export function fmt(n: number | string): string {
  return parseFloat(String(n || 0)).toFixed(2);
}

/** Format a number with K/M suffix for compact display. */
export function fmtK(n: number | string): string {
  const v = parseFloat(String(n || 0));
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v >= 1000) return (v / 1000).toFixed(0) + 'K';
  return String(Math.round(v));
}

/** Chart color palette — professional, muted tones. */
export const CHART_COLORS = [
  '#3b82f6', // blue
  '#8b5cf6', // purple
  '#f59e0b', // amber
  '#22c55e', // green
  '#ef4444', // red
  '#06b6d4', // cyan
  '#ec4899', // pink
  '#64748b', // slate
];

/** Map severity string to badge CSS class. */
export function badgeClass(severity: string): string {
  const map: Record<string, string> = {
    critical: 'badge-critical',
    high: 'badge-high',
    warning: 'badge-warning',
    ok: 'badge-ok',
    info: 'badge-info',
  };
  return map[severity] || 'badge-info';
}
