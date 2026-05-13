/**
 * My Dashboard — Tenant Member Center landing page.
 *
 * This is the "Clean and Managed" view for tenant_admin and tenant_viewer users.
 * Auto-scoped to the user's tenant_id via DashboardContext.
 *
 * Sections:
 *   1. Usage KPI cards (calls, tokens, cost, anomalies)
 *   2. Cost trend chart (last N days)
 *   3. Recent requests (last 10)
 *   4. My Virtual Keys overview
 *   5. Quick links to Invoices
 */

'use client';

import { useEffect, useState, useRef } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import { useAuth } from '@/lib/auth';
import api from '@/lib/api';
import { useRouter } from 'next/navigation';
import Script from 'next/script';

declare global {
  interface Window {
    Chart: any;
  }
}

import { fmt, fmtK } from '@/lib/utils';

export default function MyDashboardPage() {
  const { selectedTenant, period } = useDashboard();
  const { user } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [recentLogs, setRecentLogs] = useState<any[]>([]);
  const [virtualKeys, setVirtualKeys] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const trendRef = useRef<HTMLCanvasElement>(null);
  const chartsRef = useRef<Record<string, any>>({});
  const [chartLoaded, setChartLoaded] = useState(false);

  // Safely initialize chartLoaded state after mount to avoid hydration mismatch
  useEffect(() => {
    if (typeof window !== 'undefined' && !!window.Chart) {
      setChartLoaded(true);
    }
  }, []);

  const tenantId = selectedTenant || user?.tenant_id || '';

  // Fetch tenant dashboard data
  useEffect(() => {
    if (!tenantId) return;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const [dashRes, logsRes, keysRes] = await Promise.allSettled([
          api.get(`/v1/dashboard/tenant/${tenantId}?period_days=${period}`),
          api.get(`/v1/gateway/logs?tenant_id=${tenantId}&page_size=10`),
          api.get(`/v1/auth/virtual-keys?tenant_id=${tenantId}`),
        ]);

        if (dashRes.status === 'fulfilled') setData(dashRes.value.data);
        else setError('Failed to load dashboard data');

        if (logsRes.status === 'fulfilled') setRecentLogs(logsRes.value.data.logs || []);
        if (keysRes.status === 'fulfilled') {
          const keys = keysRes.value.data.keys || keysRes.value.data || [];
          setVirtualKeys(Array.isArray(keys) ? keys : []);
        }
      } catch (e: any) {
        setError(e.response?.data?.detail || e.message || 'Failed to load');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [tenantId, period]);

  // Render cost trend chart
  useEffect(() => {
    if (!data || typeof window === 'undefined' || !window.Chart || !trendRef.current) return;

    Object.values(chartsRef.current).forEach((c: any) => c?.destroy?.());
    chartsRef.current = {};

    const daily = data.cost?.daily_trend || [];
    const labels = daily.map((x: any) => String(x.date).slice(5));
    const values = daily.map((x: any) => parseFloat(x.total_cost_usd || 0));

    chartsRef.current.trend = new window.Chart(trendRef.current, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          data: values.map((v: number) => +Math.max(0, v).toFixed(2)),
          borderColor: '#1a56db',
          backgroundColor: 'rgba(26,86,219,0.07)',
          borderWidth: 2, pointRadius: 0, fill: true, tension: 0.35,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 10 }, maxTicksLimit: 8, color: '#94a3b8' } },
          y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { font: { size: 10 }, color: '#94a3b8', callback: (v: number) => `$${v.toFixed(0)}` } },
        },
      },
    });

    return () => {
      Object.values(chartsRef.current).forEach((c: any) => c?.destroy?.());
    };
  }, [data, chartLoaded]);

  // Compute KPIs
  const kCost = data ? '$' + fmt(data.cost?.total_cost_usd) : '—';
  const kCalls = data ? fmtK(data.cost?.total_calls) : '—';
  const kTokens = data ? fmtK(data.cost?.total_tokens) : '—';
  const kAnom = data ? String(data.anomalies?.total_active || 0) : '—';

  return (
    <DashboardLayout
      title="My Dashboard"
      meta={`Tenant: ${tenantId}`}
    >
      {loading && (
        <div className="loading"><div className="spinner" /> Loading your dashboard…</div>
      )}

      {error && <div className="error-state">{error}</div>}

      {!loading && !error && (
        <>
          {/* ── KPI Row ── */}
          <div className="kpi-grid-4">
            <div className="kpi">
              <div className="kpi-label">Total Cost</div>
              <div className="kpi-value">{kCost}</div>
              <div className="kpi-sub">{period} day period</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">API Calls</div>
              <div className="kpi-value">{kCalls}</div>
              <div className="kpi-sub">{tenantId}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Tokens Used</div>
              <div className="kpi-value">{kTokens}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Active Anomalies</div>
              <div className="kpi-value">{kAnom}</div>
            </div>
          </div>

          {/* ── Cost Trend + Quick Actions ── */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 16 }}>
            <div className="card">
              <div className="card-header">
                <div>
                  <div className="card-title">Cost Trend</div>
                  <div className="card-subtitle">Daily spending over {period} days</div>
                </div>
              </div>
              <div style={{ height: 220 }}>
                <canvas ref={trendRef} />
              </div>
            </div>

            {/* Quick Actions */}
            <div className="card">
              <div className="card-header">
                <div className="card-title">Quick Actions</div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <button className="btn" onClick={() => router.push('/costs')} style={{ width: '100%', textAlign: 'left' }}>
                  💰 View Cost Analytics
                </button>
                <button className="btn" onClick={() => router.push('/billing')} style={{ width: '100%', textAlign: 'left' }}>
                  🧾 My Invoices
                </button>
                <button className="btn" onClick={() => router.push('/alerts')} style={{ width: '100%', textAlign: 'left' }}>
                  🔔 Alert Settings
                </button>
                <button className="btn" onClick={() => router.push('/assistant')} style={{ width: '100%', textAlign: 'left' }}>
                  ✦ Ask AI Assistant
                </button>
              </div>
            </div>
          </div>

          {/* ── Recent Requests ── */}
          <div className="card">
            <div className="card-header">
              <div>
                <div className="card-title">Recent Requests</div>
                <div className="card-subtitle">Last 10 API calls</div>
              </div>
              <button className="btn" onClick={() => router.push('/gateway')}>
                View All Logs →
              </button>
            </div>
            {recentLogs.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--t2)', padding: '12px 0' }}>
                No recent requests found.
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Status</th>
                    <th>Tokens</th>
                    <th>Cost</th>
                    <th>Duration</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {recentLogs.map((log: any) => (
                    <tr key={log.id}>
                      <td style={{ fontWeight: 600 }}>{log.model}</td>
                      <td>
                        <span className={`badge ${log.status === 'success' ? 'badge-ok' : log.status === 'blocked' ? 'badge-critical' : 'badge-warning'}`}>
                          {log.status}
                        </span>
                      </td>
                      <td className="mono">{fmtK(log.total_tokens)}</td>
                      <td className="mono">${fmt(log.cost_usd)}</td>
                      <td className="mono">{log.duration_ms || '—'}ms</td>
                      <td style={{ fontSize: 10, color: 'var(--t3)' }}>
                        {log.created_at ? new Date(log.created_at).toLocaleTimeString() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* ── Virtual Keys ── */}
          <div className="card">
            <div className="card-header">
              <div>
                <div className="card-title">My Virtual Keys</div>
                <div className="card-subtitle">{virtualKeys.length} key{virtualKeys.length !== 1 ? 's' : ''} configured</div>
              </div>
            </div>
            {virtualKeys.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--t2)', padding: '12px 0' }}>
                No virtual keys configured. Contact your administrator to create one.
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Prefix</th>
                    <th>Environment</th>
                    <th>Budget</th>
                    <th>Used</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {virtualKeys.slice(0, 5).map((key: any) => (
                    <tr key={key.id}>
                      <td style={{ fontWeight: 600 }}>{key.name}</td>
                      <td className="mono" style={{ fontSize: 11 }}>{key.key_prefix}…</td>
                      <td>
                        <span className={`badge ${key.environment === 'live' ? 'badge-ok' : 'badge-info'}`}>
                          {key.environment}
                        </span>
                      </td>
                      <td className="mono">
                        {key.budget_usd ? `$${fmt(key.budget_usd)}` : '∞'}
                      </td>
                      <td className="mono">${fmt(key.budget_used_usd || 0)}</td>
                      <td>
                        <span className={`badge ${key.is_active ? 'badge-ok' : 'badge-critical'}`}>
                          {key.is_active ? 'active' : 'revoked'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {/* Chart.js CDN script */}
      <Script 
        src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js" 
        strategy="lazyOnload" 
        onLoad={() => setChartLoaded(true)}
      />
    </DashboardLayout>
  );
}
