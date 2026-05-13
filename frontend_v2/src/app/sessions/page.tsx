/** Session Tracing page — NEW: Phase 3 waterfall view */
'use client';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';
import { fmt, fmtK } from '@/lib/utils';
import { useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';

export default function SessionsPage() {
  const { selectedTenant } = useDashboard();
  const [sessions, setSessions] = useState<any[]>([]);
  const [waterfall, setWaterfall] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selectedModels, setSelectedModels] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const params = selectedTenant ? `?tenant_id=${selectedTenant}` : '';
      const res = await api.get(`/v1/tracing/sessions${params}`);
      setSessions(res.data.sessions || res.data || []);
    } catch (e: any) { setSessions([]); setError(e.response?.data?.detail || e.message); }
    finally { setLoading(false); }
  }, [selectedTenant]);

  useEffect(() => { load(); }, [load]);

  // Extract unique models from the data
  const dynamicModels = useMemo(() => {
    const models = new Set<string>();
    sessions.forEach(s => {
      // Check every possible field where the model name could be
      if (s.model_name) models.add(s.model_name);
      if (s.model) models.add(s.model);
      if (s.provider) models.add(s.provider);
      
      // Also check inside calls if session level is missing
      if (s.calls) {
        s.calls.forEach((c: any) => {
          if (c.model) models.add(c.model);
          if (c.model_name) models.add(c.model_name);
        });
      }
    });
    // Remove nulls/undefined and sort
    return Array.from(models).filter(Boolean).sort();
  }, [sessions]);

  const filteredSessions = sessions.filter(s => {
    const matchesSearch = s.session_id.toLowerCase().includes(search.toLowerCase());
    const modelMatch = selectedModels.length === 0 || 
                       selectedModels.includes(s.model_name) || 
                       selectedModels.includes(s.model);
    return matchesSearch && modelMatch;
  });

  const toggleModel = (model: string) => {
    setSelectedModels(prev => 
      prev.includes(model) ? prev.filter(m => m !== model) : [...prev, model]
    );
  };

  const viewWaterfall = async (sessionId: string) => {
    try {
      const res = await api.get(`/v1/tracing/sessions/${sessionId}/waterfall`);
      setWaterfall(res.data);
    } catch (e: any) { alert(e.message); }
  };

  const router = useRouter();
  const handleNewSession = () => {
    const newId = `sess_demo_${Math.random().toString(36).substring(7)}`;
    router.push(`/playground?session_id=${newId}`);
  };

  const handleExport = () => {
    const headers = ["Session ID", "Status", "Calls", "Tokens", "Cost", "Duration"];
    const rows = sessions.map(s => [s.session_id, s.status, s.total_calls, s.total_tokens, s.total_cost_usd, s.total_duration_ms]);
    const csvContent = "data:text/csv;charset=utf-8," + [headers, ...rows].map(e => e.join(",")).join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `sessions_${new Date().toISOString()}.csv`);
    document.body.appendChild(link);
    link.click();
  };

  return (
    <DashboardLayout title="Session Tracing" meta="Agent chain waterfall view">
      {loading && <div className="loading"><div className="spinner" /> Loading sessions…</div>}
      {error && <div className="error-state" style={{ margin: 16, padding: 16, borderRadius: 'var(--r)', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--red)', fontSize: 12 }}>⚠ Failed to load sessions: {error}</div>}
      {!loading && !error && (
        <div style={{ display: 'grid', gridTemplateColumns: '220px 400px 1fr', gap: 16 }}>
          {/* Sidebar Filters */}
          <div className="card" style={{ padding: 16 }}>
            <div className="card-title" style={{ fontSize: 12, marginBottom: 12 }}>Filters</div>
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 10, color: 'var(--t3)', textTransform: 'uppercase', marginBottom: 8 }}>Active Models</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {dynamicModels.length === 0 ? <div style={{ fontSize: 11, color: 'var(--t3)' }}>No models detected</div> :
                  dynamicModels.map(m => (
                    <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input 
                        type="checkbox" 
                        checked={selectedModels.includes(m)}
                        onChange={() => toggleModel(m)}
                        style={{ width: 14, height: 14 }}
                      />
                      <span style={{ fontSize: 12 }}>{m}</span>
                    </label>
                  ))
                }
              </div>
            </div>
          </div>

          {/* Feed */}
          <div className="card">
            <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div className="card-title">Sessions Feed</div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-primary" style={{ fontSize: 11, padding: '4px 8px' }} onClick={handleNewSession}>
                  + New
                </button>
                <button className="btn" style={{ fontSize: 11, padding: '4px 8px' }} onClick={load}>
                  ↻
                </button>
                <button className="btn" style={{ fontSize: 11, padding: '4px 8px' }} onClick={handleExport}>
                   CSV
                </button>
              </div>
            </div>
            <div style={{ padding: '0 12px 12px' }}>
              <input 
                type="text" 
                placeholder="Search ID..." 
                className="input" 
                style={{ width: '100%', fontSize: 12, padding: '6px 10px' }}
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            <div style={{ padding: '0 12px', height: '600px', overflowY: 'auto' }}>
              {filteredSessions.length === 0 ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>No results</div> :
                filteredSessions.map((s, i) => (
                  <div key={i} onClick={() => viewWaterfall(s.session_id)} style={{ padding: '12px 0', borderBottom: '1px solid var(--border)', cursor: 'pointer' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="mono" style={{ fontSize: 11, fontWeight: 600 }}>{s.session_id}</span>
                      <span className={`badge ${s.status === 'completed' ? 'badge-ok' : s.status === 'failed' ? 'badge-critical' : 'badge-blue'}`}>{s.status}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 12, fontSize: 10, color: 'var(--t2)', marginTop: 4 }}>
                      <span>{s.total_calls || 0} calls</span>
                      <span>{fmtK(s.total_tokens || 0)} tokens</span>
                      <span>${fmt(s.total_cost_usd || 0)}</span>
                      <span>{s.total_duration_ms || 0}ms</span>
                    </div>
                  </div>
                ))
              }
            </div>
          </div>

          {/* Waterfall */}
          <div className="card">
            <div className="card-header"><div className="card-title">Waterfall View</div></div>
            {!waterfall ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>Select a session</div> : (
              <div style={{ padding: 16 }}>
                <div className="kpi-grid-4" style={{ marginBottom: 16 }}>
                  <div className="kpi" style={{ padding: '8px 10px' }}><div className="kpi-label">Calls</div><div className="kpi-value" style={{ fontSize: 18 }}>{waterfall.session?.total_calls || 0}</div></div>
                  <div className="kpi" style={{ padding: '8px 10px' }}><div className="kpi-label">Tokens</div><div className="kpi-value" style={{ fontSize: 18 }}>{fmtK(waterfall.session?.total_tokens || 0)}</div></div>
                  <div className="kpi" style={{ padding: '8px 10px' }}><div className="kpi-label">Cost</div><div className="kpi-value" style={{ fontSize: 18 }}>${fmt(waterfall.total_cost_usd || 0)}</div></div>
                  <div className="kpi" style={{ padding: '8px 10px' }}><div className="kpi-label">Duration</div><div className="kpi-value" style={{ fontSize: 18 }}>{waterfall.total_duration_ms || 0}ms</div></div>
                </div>
                {(waterfall.calls || []).map((step: any, i: number) => {
                  const maxDur = Math.max(...(waterfall.calls || []).map((s: any) => s.duration_ms || 1));
                  const pct = (step.duration_ms / maxDur * 100).toFixed(0);
                  return (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                      <span style={{ width: 20, fontSize: 10, color: 'var(--t3)', textAlign: 'right' }}>#{i + 1}</span>
                      <span style={{ width: 120, fontSize: 11, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis' }}>{step.model || '—'}</span>
                      <div style={{ flex: 1, height: 8, background: 'var(--card2)', borderRadius: 4, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: step.status === 'error' ? 'var(--red)' : 'var(--acc)', borderRadius: 4, transition: 'width 0.3s ease' }} />
                      </div>
                      <span className="mono" style={{ fontSize: 10, minWidth: 50, textAlign: 'right' }}>{step.duration_ms}ms</span>
                      <span className="mono" style={{ fontSize: 10, minWidth: 50, textAlign: 'right' }}>${fmt(step.cost_usd || 0)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}
