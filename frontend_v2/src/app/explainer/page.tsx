/** AI Explainer page (M4) — replaces loadM4() + explainAnomaly() */
'use client';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';

export default function ExplainerPage() {
  const { selectedTenant, period } = useDashboard();
  const [anomalies, setAnomalies] = useState<any[]>([]);
  const [explanation, setExplanation] = useState<any>(null);
  const [explaining, setExplaining] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const load = async () => {
      setLoading(true); setError('');
      try {
        const res = await api.get('/v1/detection/anomalies?hours=24');
        let recs = Array.isArray(res.data) ? res.data : (res.data.anomalies || []);
        if (selectedTenant) recs = recs.filter((a: any) => a.tenant_id === selectedTenant);
        setAnomalies(recs.filter((a: any) => !a.resolved));
      } catch (e: any) { setAnomalies([]); setError(e.response?.data?.detail || e.message); }
      finally { setLoading(false); }
    };
    load();
  }, [selectedTenant]);

  const explain = async (a: any) => {
    setExplaining(true); setExplanation(null);
    try {
      const q = `Explain the anomaly: ${a.anomaly_type} for tenant ${a.tenant_id}. The anomaly id is ${a.id}. What caused it and what should we do?`;
      const res = await api.get(`/v1/dashboard/assistant/ask?question=${encodeURIComponent(q)}&tenant_id=${a.tenant_id}&period_days=${period}`);
      setExplanation({ anomaly: a, ...res.data });
    } catch (e: any) { setExplanation({ anomaly: a, answer: 'Error: ' + e.message }); }
    finally { setExplaining(false); }
  };

  return (
    <DashboardLayout title="AI Explainer" meta="AI-powered anomaly analysis">
      {loading && <div className="loading"><div className="spinner" /> Loading…</div>}
      {error && <div className="error-state" style={{ margin: 16, padding: 16, borderRadius: 'var(--r)', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--red)', fontSize: 12 }}>⚠ Failed to load anomalies: {error}</div>}
      {!loading && !error && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="card">
            <div className="card-header"><div className="card-title">Anomalies to Explain</div></div>
            {anomalies.length === 0 ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>No active anomalies</div> :
              anomalies.map((a, i) => (
                <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className={`badge badge-${a.severity || 'warning'}`}>{a.severity}</span>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{a.tenant_id}</span>
                    <span style={{ fontSize: 10, color: 'var(--t2)' }}>{a.anomaly_type}</span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--t2)', marginTop: 2 }}>{a.description || a.anomaly_type}</div>
                  <button className="btn btn-primary" style={{ fontSize: 10, padding: '3px 10px', marginTop: 6 }} onClick={() => explain(a)}>Explain ✦</button>
                </div>
              ))
            }
          </div>
          <div className="card">
            <div className="card-header"><div className="card-title">AI Explanation</div></div>
            {explaining && <div className="loading"><div className="spinner" /> Thinking…</div>}
            {!explaining && !explanation && <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>Select an anomaly to explain</div>}
            {!explaining && explanation && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{explanation.anomaly?.anomaly_type} — {explanation.anomaly?.tenant_id}</div>
                <div style={{ fontSize: 12, color: 'var(--t2)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{explanation.answer}</div>
                {explanation.key_figures?.length > 0 && (
                  <div style={{ display: 'flex', gap: 16, marginTop: 10, flexWrap: 'wrap' }}>
                    {explanation.key_figures.map((f: any, i: number) => (
                      <div key={i} style={{ padding: '8px 12px', background: 'var(--card2)', borderRadius: 'var(--r)' }}>
                        <div style={{ fontSize: 16, fontWeight: 700 }}>{f.value}</div>
                        <div style={{ fontSize: 10, color: 'var(--t3)' }}>{f.label}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}
