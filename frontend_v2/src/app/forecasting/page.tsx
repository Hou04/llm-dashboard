/** Forecasting page (M6) — replaces loadM6() */
'use client';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';

export default function ForecastingPage() {
  const { selectedTenant } = useDashboard();
  const [risks, setRisks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true); setError('');
    try {
      const res = await api.get('/v1/forecasting/budget-risk');
      let r = Array.isArray(res.data) ? res.data : (res.data.risks || []);
      if (selectedTenant) r = r.filter((x: any) => x.tenant_id === selectedTenant);
      setRisks(r);
    } catch (e: any) { setRisks([]); setError(e.response?.data?.detail || e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [selectedTenant]);

  return (
    <DashboardLayout title="Forecasting & Budget Risk" meta="Token usage forecasting">
      {loading && <div className="loading"><div className="spinner" /> Loading…</div>}
      {error && <div className="error-state" style={{ margin: 16, padding: 16, borderRadius: 'var(--r)', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--red)', fontSize: 12 }}>⚠ Failed to load forecasts: {error}</div>}
      {!loading && !error && (
        <div className="card">
          <div className="card-header">
            <div className="card-title">Budget Risk Assessment</div>
            {selectedTenant && <button className="btn btn-primary" style={{ fontSize: 11 }} onClick={async () => { try { await api.get(`/v1/forecasting/forecast/${selectedTenant}`); } catch {} load(); }}>Generate Forecast</button>}
          </div>
          {risks.length === 0 ? <div style={{ padding: 12, fontSize: 12, color: 'var(--t2)' }}>No budget risks detected ✓</div> :
            risks.map((r, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '7px 0', borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{r.tenant_id}</div>
                  <div style={{ fontSize: 10, color: 'var(--t2)' }}>{r.risk_type} · {r.days_until_exhaustion}d</div>
                </div>
                <span className={`badge badge-${r.urgency || 'warning'}`}>{r.urgency || 'warning'}</span>
              </div>
            ))
          }
        </div>
      )}
    </DashboardLayout>
  );
}
