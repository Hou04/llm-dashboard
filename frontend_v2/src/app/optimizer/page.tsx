/** Model Optimizer page (M5) — replaces loadM5() */
'use client';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';
import { fmt } from '@/lib/utils';

export default function OptimizerPage() {
  const { selectedTenant } = useDashboard();
  const [recs, setRecs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true); setError('');
    try {
      const res = await api.get('/v1/forecasting/optimize/all/models');
      let r = Array.isArray(res.data) ? res.data : (res.data.recommendations || []);
      if (selectedTenant) r = r.filter((x: any) => x.tenant_id === selectedTenant);
      setRecs(r);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [selectedTenant]);

  const proposed = recs.filter(r => r.status === 'proposed').length;
  const applied = recs.filter(r => r.status === 'applied').length;
  const savings = recs.reduce((s, r) => s + parseFloat(r.expected_monthly_saving_usd || 0), 0);

  return (
    <DashboardLayout title="Model Optimizer" meta="Cost optimization recommendations">
      {loading && <div className="loading"><div className="spinner" /> Loading…</div>}
      {error && <div className="error-state">{error}</div>}
      {!loading && !error && (
        <>
          <div className="kpi-grid-4">
            <div className="kpi"><div className="kpi-label">Model Proposals</div><div className="kpi-value">{proposed}</div></div>
            <div className="kpi"><div className="kpi-label">Applied</div><div className="kpi-value">{applied}</div></div>
            <div className="kpi"><div className="kpi-label">Prompt Proposals</div><div className="kpi-value">—</div></div>
            <div className="kpi"><div className="kpi-label">Est. Savings</div><div className="kpi-value">${fmt(savings)}</div></div>
          </div>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Optimization Recommendations</div>
              {selectedTenant && <button className="btn btn-primary" style={{ fontSize: 11 }} onClick={async () => { try { await api.get(`/v1/forecasting/optimize/${selectedTenant}`); } catch {} load(); }}>Run Optimizer</button>}
            </div>
            {recs.length === 0 ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>No recommendations available</div> :
              recs.slice(0, 10).map((r, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>{r.agent_id} <span style={{ color: 'var(--t2)', fontWeight: 400 }}>({r.tenant_id})</span></div>
                    <div style={{ fontSize: 10, color: 'var(--t2)', marginTop: 2 }}>{r.current_model} → {r.recommended_model}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: parseFloat(r.expected_monthly_saving_usd || 0) > 0 ? 'var(--green)' : 'var(--amber)' }}>−${Math.abs(parseFloat(r.expected_monthly_saving_usd || 0)).toFixed(2)}/mo</div>
                    <span className="badge badge-warning" style={{ fontSize: 10 }}>{r.status}</span>
                  </div>
                </div>
              ))
            }
          </div>
        </>
      )}
    </DashboardLayout>
  );
}
