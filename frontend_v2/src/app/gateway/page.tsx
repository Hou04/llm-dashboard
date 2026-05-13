/**
 * API Gateway page (M1) — replaces loadM1()
 */
'use client';

import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';
import { fmt, fmtK } from '@/lib/utils';

export default function GatewayPage() {
  const { selectedTenant, period } = useDashboard();
  const [usage, setUsage] = useState<any>(null);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tenantIds, setTenantIds] = useState<string[]>([]);

  // Dynamically fetch tenant list
  useEffect(() => {
    const fetchTenants = async () => {
      try {
        const res = await api.get('/v1/dashboard/tenants');
        const list = (res.data.tenants || res.data || []).map(
          (t: any) => typeof t === 'string' ? t : t.tenant_id || ''
        ).filter(Boolean);
        setTenantIds(list);
      } catch { setTenantIds([]); setError('Failed to load tenant list'); }
    };
    fetchTenants();
  }, []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const tenantsToFetch = selectedTenant ? [selectedTenant] : tenantIds;
        if (tenantsToFetch.length === 0) { setLoading(false); return; }
        const [decs, ...rows] = await Promise.all([
          api.get('/v1/gateway/governance/decisions' + (selectedTenant ? '/' + selectedTenant : '') + '?hours=168&limit=50'),
          ...tenantsToFetch.map(t => api.get(`/v1/gateway/usage/${t}`).catch(() => ({ data: { tenant_id: t, total_calls: 0 } }))),
        ]);

        const u = { total_calls: 0, successful_calls: 0, failed_calls: 0, avg_duration_ms: 0 };
        let totalAvg = 0;
        rows.forEach((r: any) => {
          const d = r.data || r;
          u.total_calls += d.total_calls || 0;
          u.successful_calls += d.successful_calls || 0;
          u.failed_calls += (d.total_calls || 0) - (d.successful_calls || 0);
          totalAvg += d.avg_duration_ms || 0;
        });
        u.avg_duration_ms = rows.length ? totalAvg / rows.length : 0;
        setUsage({ ...u, rows: rows.map((r: any) => r.data || r), tenants: tenantsToFetch });
        setDecisions((decs.data?.decisions || []).slice(0, 15));
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [selectedTenant, period, tenantIds]);

  return (
    <DashboardLayout title="API Gateway" meta="Request monitoring & governance decisions">
      {loading && <div className="loading"><div className="spinner" /> Loading gateway data…</div>}
      {error && <div className="error-state">{error}</div>}
      {!loading && !error && usage && (
        <>
          <div className="kpi-grid-4">
            <div className="kpi"><div className="kpi-label">Total Calls</div><div className="kpi-value">{fmtK(usage.total_calls)}</div></div>
            <div className="kpi"><div className="kpi-label">Successful</div><div className="kpi-value">{fmtK(usage.successful_calls)}</div></div>
            <div className="kpi"><div className="kpi-label">Error Rate</div><div className="kpi-value">{usage.total_calls > 0 ? ((usage.failed_calls / usage.total_calls) * 100).toFixed(1) + '%' : '0%'}</div></div>
            <div className="kpi"><div className="kpi-label">Avg Latency</div><div className="kpi-value">{usage.avg_duration_ms ? Math.round(usage.avg_duration_ms) + 'ms' : '—'}</div></div>
          </div>

          <div className="card">
            <div className="card-header"><div className="card-title">Tenant Usage</div></div>
            {(usage.rows || []).map((r: any, i: number) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                <span style={{ flex: 1, fontSize: 12, fontWeight: 600 }}>{r.tenant_id || usage.tenants[i]}</span>
                <span style={{ fontSize: 11, color: 'var(--t2)' }}>{fmtK(r.total_calls || 0)} calls</span>
                <span className="mono" style={{ fontSize: 11, fontWeight: 700 }}>${fmt(r.total_cost_usd || 0)}</span>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-header"><div className="card-title">Recent Governance Decisions</div></div>
            {decisions.length === 0 ? (
              <div style={{ padding: 12, fontSize: 12, color: 'var(--t2)' }}>No decisions recorded (checked 7 days)</div>
            ) : (
              <table>
                <thead><tr><th>Tenant</th><th>Decision</th><th>Model</th><th>Tokens</th><th>Time</th></tr></thead>
                <tbody>
                  {decisions.map((d: any, i: number) => (
                    <tr key={i}>
                      <td>{d.tenant_id || '—'}</td>
                      <td><span className={`badge ${d.decision_type === 'block' ? 'badge-critical' : d.decision_type === 'allow_downgrade' ? 'badge-warning' : 'badge-ok'}`}>{d.decision_type}</span></td>
                      <td style={{ color: 'var(--t2)' }}>{d.model_used || '—'}</td>
                      <td>{fmtK(d.total_tokens || 0)}</td>
                      <td>{d.decided_at ? new Date(d.decided_at).toLocaleTimeString() : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </DashboardLayout>
  );
}
