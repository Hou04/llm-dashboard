/**
 * Cost Analytics page (M2) — replaces loadM2()
 */
'use client';

import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';
import { fmt, fmtK, CHART_COLORS } from '@/lib/utils';

export default function CostsPage() {
  const { selectedTenant, period } = useDashboard();
  const [sum, setSum] = useState<any>(null);
  const [models, setModels] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
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
      setLoading(true); setError('');
      try {
        const tenantsToFetch = selectedTenant ? [selectedTenant] : tenantIds;
        if (tenantsToFetch.length === 0) { setLoading(false); return; }
        const results = await Promise.all(tenantsToFetch.map(t =>
          Promise.all([
            api.get(`/v1/analytics/costs/summary/${t}?period_days=${period}`).catch(() => ({ data: null })),
            api.get(`/v1/analytics/costs/models/${t}?period_days=${period}`).catch(() => ({ data: null })),
            api.get(`/v1/analytics/costs/agents/${t}?period_days=${period}`).catch(() => ({ data: null })),
          ])
        ));

        const s = { total_cost_usd: 0, total_calls: 0, error_calls: 0, avg_duration_ms: 0 };
        const modelMap: Record<string, number> = {};
        const agentMap: Record<string, { cost: number; calls: number }> = {};
        let count = 0;

        results.forEach(([sumR, modR, agR]) => {
          const sd = sumR.data;
          if (sd) { s.total_cost_usd += parseFloat(sd.total_cost_usd || 0); s.total_calls += sd.total_calls || 0; s.error_calls += sd.error_calls || 0; s.avg_duration_ms += sd.avg_duration_ms || 0; count++; }
          const md = modR.data; if (md) { const arr = Array.isArray(md) ? md : (md.models || []); arr.forEach((m: any) => { const k = `${m.model}|${m.provider}`; modelMap[k] = (modelMap[k] || 0) + parseFloat(m.total_cost_usd || 0); }); }
          const ad = agR.data; if (ad) { const arr = Array.isArray(ad) ? ad : (ad.agents || []); arr.forEach((a: any) => { if (!agentMap[a.agent_id]) agentMap[a.agent_id] = { cost: 0, calls: 0 }; agentMap[a.agent_id].cost += parseFloat(a.total_cost_usd || 0); agentMap[a.agent_id].calls += a.total_calls || 0; }); }
        });

        if (count > 0) s.avg_duration_ms /= count;
        setSum(s);

        const totalModelCost = Object.values(modelMap).reduce((a, b) => a + b, 0);
        setModels(Object.entries(modelMap).sort(([, a], [, b]) => b - a).slice(0, 6).map(([k, c]) => {
          const [model, provider] = k.split('|');
          return { model, provider, cost: c, share: totalModelCost ? (c / totalModelCost * 100).toFixed(1) : '0' };
        }));

        setAgents(Object.entries(agentMap).sort(([, a], [, b]) => b.cost - a.cost).slice(0, 6).map(([k, v]) => ({ agent_id: k, ...v })));
      } catch (e: any) { setError(e.message); }
      finally { setLoading(false); }
    };
    load();
  }, [selectedTenant, period, tenantIds]);

  return (
    <DashboardLayout title="Cost Analytics" meta={`${period}-day breakdown`}>
      {loading && <div className="loading"><div className="spinner" /> Loading cost data…</div>}
      {error && <div className="error-state">{error}</div>}
      {!loading && !error && sum && (
        <>
          <div className="kpi-grid-4">
            <div className="kpi"><div className="kpi-label">Total Spend</div><div className="kpi-value">${fmt(sum.total_cost_usd)}</div></div>
            <div className="kpi"><div className="kpi-label">Total Calls</div><div className="kpi-value">{fmtK(sum.total_calls)}</div></div>
            <div className="kpi"><div className="kpi-label">Error Rate</div><div className="kpi-value">{sum.total_calls > 0 ? ((sum.error_calls / sum.total_calls) * 100).toFixed(1) + '%' : '0%'}</div></div>
            <div className="kpi"><div className="kpi-label">Avg Latency</div><div className="kpi-value">{sum.avg_duration_ms ? Math.round(sum.avg_duration_ms) + 'ms' : '—'}</div></div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div className="card">
              <div className="card-header"><div className="card-title">Model Breakdown</div></div>
              {models.map((m, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ flex: 1, fontSize: 12 }}>{m.model}</span>
                  <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 5, background: 'var(--card2)', color: 'var(--t2)' }}>{m.provider}</span>
                  <div style={{ flex: 2, height: 5, background: 'var(--card2)', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${m.share}%`, background: CHART_COLORS[i % CHART_COLORS.length], borderRadius: 3 }} />
                  </div>
                  <span className="mono" style={{ fontSize: 11, fontWeight: 700, minWidth: 54, textAlign: 'right' }}>${fmt(m.cost)}</span>
                  <span style={{ fontSize: 10, color: 'var(--t3)', minWidth: 34, textAlign: 'right' }}>{m.share}%</span>
                </div>
              ))}
            </div>

            <div className="card">
              <div className="card-header"><div className="card-title">Agent Breakdown</div></div>
              {agents.map((a, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ flex: 1, fontSize: 12 }}>{a.agent_id}</span>
                  <span className="mono" style={{ fontSize: 11, fontWeight: 700 }}>${fmt(a.cost)}</span>
                  <span style={{ fontSize: 10, color: 'var(--t3)' }}>{fmtK(a.calls)} calls</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </DashboardLayout>
  );
}
