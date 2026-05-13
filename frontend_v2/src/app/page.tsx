/**
 * Executive Overview — The main dashboard page (replaces loadExec).
 *
 * Shows: 4 KPI cards, Cost Trend chart, Tenant Ranking, Model Distribution,
 *        Active Anomalies, Forecasts, Governance summary, Optimization.
 *
 * Two-Personality: Non-super-admin users are redirected to /my-dashboard.
 */

'use client';

import { useEffect, useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import Script from 'next/script';
import { useDashboard } from '@/lib/dashboard-context';
import { useAuth } from '@/lib/auth';
import api from '@/lib/api';
import { fmt, fmtK, CHART_COLORS, badgeClass } from '@/lib/utils';

// Chart.js loaded from CDN for compatibility with existing chart logic
declare global {
  interface Window {
    Chart: any;
  }
}

export default function ExecutivePage() {
  const { selectedTenant, period } = useDashboard();
  const { user, isAuthenticated } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const trendRef = useRef<HTMLCanvasElement>(null);
  const doughnutRef = useRef<HTMLCanvasElement>(null);
  const chartsRef = useRef<Record<string, any>>({});
  const [chartLoaded, setChartLoaded] = useState(false);

  // Safely initialize chartLoaded state after mount to avoid hydration mismatch
  useEffect(() => {
    if (typeof window !== 'undefined' && !!window.Chart) {
      setChartLoaded(true);
    }
  }, []);

  // Two-Personality Routing:
  // If user is NOT a super_admin, redirect them to their member center.
  useEffect(() => {
    if (isAuthenticated && user && user.role !== 'super_admin') {
      router.push('/my-dashboard');
    }
  }, [isAuthenticated, user, router]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        if (selectedTenant) {
          const d = await api.get(`/v1/dashboard/tenant/${selectedTenant}?period_days=${period}`);
          setData({ type: 'tenant', ...d.data });
        } else {
          const d = await api.get(`/v1/dashboard/executive?period_days=${period}`);
          setData({ type: 'global', ...d.data });
        }
      } catch (e: any) {
        setError(e.response?.data?.detail || e.message || 'Failed to load');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [selectedTenant, period]);

  // ── Render charts when data changes ──
  useEffect(() => {
    if (!data || typeof window === 'undefined' || !window.Chart) return;

    // Destroy old charts
    Object.values(chartsRef.current).forEach((c: any) => c?.destroy?.());
    chartsRef.current = {};

    // Cost trend
    if (trendRef.current) {
      let labels: string[], values: number[];
      if (data.type === 'tenant') {
        const daily = data.cost?.daily_trend || [];
        labels = daily.map((x: any) => String(x.date).slice(5));
        values = daily.map((x: any) => parseFloat(x.total_cost_usd || 0));
      } else {
        const daily = data.summary?.daily_trend || [];
        labels = daily.map((x: any) => String(x.date).slice(5));
        values = daily.map((x: any) => parseFloat(x.total_cost_usd || 0));
      }

      chartsRef.current.trend = new window.Chart(trendRef.current, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            data: values.map(v => +Math.max(0, v).toFixed(2)),
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59, 130, 246, 0.06)',
            borderWidth: 2, pointRadius: 0, fill: true, tension: 0.4,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false }, ticks: { font: { size: 10 }, maxTicksLimit: 8, color: '#a1a1aa' } },
            y: { grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 10 }, color: '#a1a1aa', callback: (v: number) => `$${v.toFixed(0)}` } },
          },
        },
      });
    }

    // Doughnut (tenant mode only)
    if (doughnutRef.current && data.type === 'tenant') {
      const md = data.cost?.model_breakdown || [];
      if (md.length > 0) {
        chartsRef.current.doughnut = new window.Chart(doughnutRef.current, {
          type: 'doughnut',
          data: {
            labels: md.map((x: any) => x.model),
            datasets: [{ data: md.map((x: any) => parseFloat(x.cost_share_pct)), backgroundColor: CHART_COLORS, borderWidth: 0 }],
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            cutout: '72%',
            plugins: { legend: { display: false } },
          },
        });
      }
    }

    return () => {
      Object.values(chartsRef.current).forEach((c: any) => c?.destroy?.());
    };
  }, [data, period, chartLoaded]);

  if (!isAuthenticated || (user && user.role !== 'super_admin')) {
    return (
      <DashboardLayout title="Redirecting" meta="Switching cockpit mode...">
        <div style={{ 
          display: 'flex', 
          flexDirection: 'column', 
          alignItems: 'center', 
          justifyContent: 'center', 
          padding: 60,
          color: 'var(--t3)'
        }}>
          <div className="spinner" style={{ marginBottom: 12 }} />
          Redirecting to your personal dashboard...
        </div>
      </DashboardLayout>
    );
  }

  // ── Compute KPIs ──
  let kCost = '—', kCalls = '—', kTokens = '—', kAnom = '—';
  let tenantRows: any[] = [];
  let anomalies: any[] = [];

  if (data) {
    if (data.type === 'tenant') {
      kCost = '$' + fmt(data.cost?.total_cost_usd);
      kCalls = fmtK(data.cost?.total_calls);
      kTokens = fmtK(data.cost?.total_tokens);
      kAnom = String(data.anomalies?.total_active || 0);
      anomalies = data.anomalies?.active || [];
    } else {
      const s = data.summary || {};
      kCost = '$' + fmt(s.total_cost_usd);
      let tc = 0, tt = 0;
      (data.tenants || []).forEach((t: any) => { tc += t.usage?.total_calls || 0; tt += t.usage?.total_tokens || 0; });
      kCalls = fmtK(tc);
      kTokens = fmtK(tt);
      kAnom = String(s.active_anomalies || 0);
      tenantRows = data.tenants || [];
      anomalies = tenantRows.filter((t: any) => t.anomaly_status?.has_active_anomaly);
    }
  }

  return (
    <DashboardLayout
      title="Executive Overview"
      meta={selectedTenant ? `Tenant: ${selectedTenant}` : 'All tenants'}
    >
      {loading && (
        <div className="loading"><div className="spinner" /> Loading dashboard…</div>
      )}

      {error && <div className="error-state">{error}</div>}

      {!loading && !error && data && (
        <>
          {/* KPI Row */}
          <div className="kpi-grid-4">
            <div className="kpi">
              <div className="kpi-label">Total Cost</div>
              <div className="kpi-value">{kCost}</div>
              <div className="kpi-sub">{period} day period</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">API Calls</div>
              <div className="kpi-value">{kCalls}</div>
              <div className="kpi-sub">{selectedTenant || 'All tenants'}</div>
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

          {/* Charts Row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 16 }}>
            {/* Cost Trend */}
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

            {/* Tenant Ranking / Model Distribution */}
            <div className="card">
              <div className="card-header">
                <div className="card-title">
                  {data.type === 'tenant' ? 'Model Distribution' : 'Tenant Ranking'}
                </div>
              </div>
              {data.type === 'tenant' ? (
                <div style={{ height: 180 }}>
                  <canvas ref={doughnutRef} />
                </div>
              ) : (
                <div>
                  {tenantRows.map((t: any, i: number) => {
                    const mx = Math.max(...tenantRows.map((r: any) => parseFloat(r.cost?.total_usd || 0)));
                    const pct = mx ? (parseFloat(t.cost?.total_usd || 0) / mx * 100).toFixed(0) : '0';
                    return (
                      <div key={t.tenant_id} style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: '8px 0', borderBottom: '1px solid var(--border)',
                      }}>
                        <span style={{ flex: 1, fontSize: 12, fontWeight: 600 }}>{t.tenant_id}</span>
                        <div style={{ flex: 2, height: 4, background: 'var(--bg2)', borderRadius: 100, overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${pct}%`, background: CHART_COLORS[i % CHART_COLORS.length], borderRadius: 100 }} />
                        </div>
                        <span className="mono" style={{ fontSize: 11, fontWeight: 700, minWidth: 56, textAlign: 'right' }}>
                          ${fmt(t.cost?.total_usd)}
                        </span>
                        <span className={`badge ${badgeClass(t.anomaly_status?.severity || 'ok')}`}>
                          {t.anomaly_status?.severity || 'ok'}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Anomalies */}
          <div className="card">
            <div className="card-header">
              <div className="card-title">Active Anomalies</div>
            </div>
            {anomalies.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--t3)', padding: '12px 0' }}>No active anomalies ✓</div>
            ) : (
              anomalies.slice(0, 5).map((a: any, i: number) => (
                <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span className={`badge ${badgeClass(a.severity || a.anomaly_status?.severity || 'warning')}`}>
                      {a.severity || a.anomaly_status?.severity || 'warning'}
                    </span>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>
                      {a.anomaly_type || a.tenant_id}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--t2)' }}>
                    {a.description || `${a.anomaly_status?.count || 0} anomalies`}
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}

      {/* Chart.js CDN script */}
      <Script 
        src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js" 
        strategy="afterInteractive"
        onLoad={() => setChartLoaded(true)}
      />
    </DashboardLayout>
  );
}
