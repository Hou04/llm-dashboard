/** Anomaly Detection page (M3) — replaces loadM3() */
'use client';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';

export default function AnomaliesPage() {
  const { selectedTenant } = useDashboard();
  const [anomalies, setAnomalies] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true); setError('');
    try {
      const res = await api.get('/v1/detection/anomalies?hours=24');
      let recs = Array.isArray(res.data) ? res.data : (res.data.anomalies || []);
      if (selectedTenant) recs = recs.filter((a: any) => a.tenant_id === selectedTenant);
      setAnomalies(recs);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [selectedTenant]);

  const active = anomalies.filter(a => !a.resolved);
  const resolved = anomalies.filter(a => a.resolved);

  const resolveAnomaly = async (id: string) => {
    try { await api.patch(`/v1/detection/anomalies/${id}/resolve`); load(); } catch (e: any) { alert(e.message); }
  };

  return (
    <DashboardLayout title="Anomaly Detection" meta="24-hour window">
      {loading && <div className="loading"><div className="spinner" /> Loading anomalies…</div>}
      {error && <div className="error-state">{error}</div>}
      {!loading && !error && (
        <>
          <div className="kpi-grid-4">
            <div className="kpi"><div className="kpi-label">Active</div><div className="kpi-value">{active.length}</div></div>
            <div className="kpi"><div className="kpi-label">Resolved</div><div className="kpi-value">{resolved.length}</div></div>
            <div className="kpi"><div className="kpi-label">Critical</div><div className="kpi-value">{active.filter(a => a.severity === 'critical').length}</div></div>
            <div className="kpi"><div className="kpi-label">High</div><div className="kpi-value">{active.filter(a => a.severity === 'high').length}</div></div>
          </div>
          <div className="card">
            <div className="card-header"><div className="card-title">Active Anomalies</div></div>
            {active.length === 0 ? <div style={{ padding: 20, textAlign: 'center', color: 'var(--t2)', fontSize: 12 }}>No active anomalies ✓</div> :
              active.map((a, i) => {
                const pct = ((a.observed_value - a.baseline_mean) / Math.max(a.baseline_mean, 1) * 100).toFixed(0);
                return (
                  <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                      <span className={`badge badge-${a.severity || 'warning'}`}>{a.severity}</span>
                      <span style={{ fontSize: 12, fontWeight: 600 }}>{a.tenant_id}</span>
                      <span style={{ fontSize: 10, color: 'var(--t3)', marginLeft: 'auto' }}>{a.vote_count || 0}/3 · Z={parseFloat(a.stl_residual_zscore || 0).toFixed(2)}</span>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--t2)' }}>{a.anomaly_type} · {pct}% · {a.description || ''}</div>
                    <div style={{ marginTop: 6 }}>
                      <button className="btn" style={{ fontSize: 10, padding: '3px 8px' }} onClick={() => resolveAnomaly(a.id)}>Resolve ✓</button>
                    </div>
                  </div>
                );
              })
            }
          </div>
        </>
      )}
    </DashboardLayout>
  );
}
