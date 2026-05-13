/** A/B Experiments page — NEW: Phase 3 prompt A/B testing */
'use client';
import { useEffect, useState, useCallback } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';
import { fmt } from '@/lib/utils';
import { IconPlus } from '@/components/Icons';
import styles from '../models/Models.module.css';

export default function ExperimentsPage() {
  const { selectedTenant } = useDashboard();
  const [experiments, setExperiments] = useState<any[]>([]);
  const [results, setResults] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Modal State
  const [isCreating, setIsCreating] = useState(false);
  const [newExp, setNewExp] = useState({
    name: '',
    prompt_name: '',
    variant_a_version: 1,
    variant_b_version: 2,
    traffic_split: 50,
    primary_metric: 'cost',
    description: ''
  });

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const params = selectedTenant ? `?tenant_id=${selectedTenant}` : '';
      const res = await api.get(`/v1/tracing/experiments${params}`);
      setExperiments(res.data.experiments || res.data || []);
    } catch (e: any) { setExperiments([]); setError(e.response?.data?.detail || e.message); }
    finally { setLoading(false); }
  }, [selectedTenant]);

  useEffect(() => { load(); }, [load]);

  const viewResults = async (id: string) => {
    try { const res = await api.get(`/v1/tracing/experiments/${id}/results`); setResults(res.data); }
    catch (e: any) { alert(e.message); }
  };

  const startExp = async (id: string) => { try { await api.post(`/v1/tracing/experiments/${id}/start`); load(); } catch (e: any) { alert(e.message); } };
  const pauseExp = async (id: string) => { try { await api.post(`/v1/tracing/experiments/${id}/pause`); load(); } catch (e: any) { alert(e.message); } };

  const handleCreate = async () => {
    try {
      const payload = {
        ...newExp,
        traffic_split: newExp.traffic_split / 100, // convert 50 to 0.5
        min_samples: 50,
        tenant_id: selectedTenant
      };
      await api.post(`/v1/tracing/experiments`, payload);
      setIsCreating(false);
      setNewExp({ name: '', prompt_name: '', variant_a_version: 1, variant_b_version: 2, traffic_split: 50, primary_metric: 'cost', description: '' });
      load();
    } catch (e: any) {
      alert(e.response?.data?.detail || e.message);
    }
  };

  return (
    <DashboardLayout title="A/B Experiments" meta="Prompt comparison with statistical significance">
      {loading && <div className="loading"><div className="spinner" /> Loading experiments…</div>}
      {error && <div className="error-state" style={{ margin: 16, padding: 16, borderRadius: 'var(--r)', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--red)', fontSize: 12 }}>⚠ Failed to load experiments: {error}</div>}
      {!loading && !error && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="card">
            <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div className="card-title">Experiments</div>
              <button className="btn btn-primary" style={{ fontSize: 11, padding: '4px 10px', display: 'flex', gap: 4, alignItems: 'center' }} onClick={() => setIsCreating(true)}>
                <IconPlus size={12} /> Create Experiment
              </button>
            </div>
            {experiments.length === 0 ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>No experiments found. Click Create Experiment to start.</div> :
              experiments.map((e, i) => (
                <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{e.name}</span>
                    <span className={`badge ${e.status === 'running' ? 'badge-ok' : e.status === 'completed' ? 'badge-blue' : 'badge-info'}`}>{e.status}</span>
                    {e.winner && <span className="badge badge-ok">Winner: {e.winner}</span>}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--t2)', marginTop: 2 }}>{e.prompt_name} · v{e.variant_a_version} vs v{e.variant_b_version} · {(e.traffic_split * 100).toFixed(0)}% split</div>
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <button className="btn" style={{ fontSize: 10, padding: '2px 8px' }} onClick={() => viewResults(e.id)}>Results</button>
                    {e.status === 'draft' && <button className="btn btn-primary" style={{ fontSize: 10, padding: '2px 8px' }} onClick={() => startExp(e.id)}>Start</button>}
                    {e.status === 'running' && <button className="btn" style={{ fontSize: 10, padding: '2px 8px' }} onClick={() => pauseExp(e.id)}>Pause</button>}
                  </div>
                </div>
              ))
            }
          </div>

          <div className="card">
            <div className="card-header"><div className="card-title">Experiment Results</div></div>
            {!results ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>Click an experiment to view results</div> : (
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>{results.experiment?.name || results.name}</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  {['A', 'B'].map(variant => {
                    const prefix = variant.toLowerCase();
                    const exp = results.experiment || results;
                    const isWinner = (results.experiment?.winner || results.winner) === variant;
                    return (
                      <div key={variant} style={{ padding: 12, background: isWinner ? 'rgba(16,185,129,0.08)' : 'var(--card2)', borderRadius: 'var(--r)', border: isWinner ? '2px solid var(--green)' : '1px solid var(--border)' }}>
                        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Variant {variant} {isWinner && '👑'}</div>
                        <div style={{ fontSize: 11, color: 'var(--t2)', display: 'grid', gap: 4 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Requests</span><span className="mono" style={{ fontWeight: 600 }}>{exp[`${prefix}_requests`] || 0}</span></div>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Avg Latency</span><span className="mono" style={{ fontWeight: 600 }}>{fmt(exp[`${prefix}_avg_latency_ms`])}ms</span></div>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Avg Cost</span><span className="mono" style={{ fontWeight: 600 }}>${fmt(exp[`${prefix}_avg_cost_usd`])}</span></div>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Avg Quality</span><span className="mono" style={{ fontWeight: 600 }}>{fmt(exp[`${prefix}_avg_quality`])}</span></div>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Errors</span><span className="mono" style={{ fontWeight: 600 }}>{exp[`${prefix}_error_count`] || 0}</span></div>
                        </div>
                      </div>
                    );
                  })}
                </div>
                {results.p_value != null && (
                  <div style={{ marginTop: 12, padding: 10, background: 'var(--card2)', borderRadius: 'var(--r)', fontSize: 11, color: 'var(--t2)' }}>
                    <strong>Statistical significance:</strong> p-value = {parseFloat(results.p_value).toFixed(4)} · Confidence: {results.confidence_level ? (results.confidence_level * 100).toFixed(1) + '%' : '—'}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Create Experiment Modal */}
      {isCreating && (
        <div className={styles.modalOverlay} onClick={() => setIsCreating(false)}>
          <div className={styles.modal} onClick={e => e.stopPropagation()}>
            <div className={styles.modalTitle}>Create A/B Experiment</div>
            
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Experiment Name</label>
              <input 
                type="text" className={styles.modalInput} placeholder="e.g. GPT-4 vs Haiku Test"
                value={newExp.name} onChange={(e) => setNewExp({...newExp, name: e.target.value})}
                autoFocus style={{ fontFamily: 'inherit' }}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Prompt CMS Target</label>
              <input 
                type="text" className={styles.modalInput} placeholder="e.g. system_prompt"
                value={newExp.prompt_name} onChange={(e) => setNewExp({...newExp, prompt_name: e.target.value})}
                style={{ fontFamily: 'inherit' }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Variant A (Version)</label>
                <input 
                  type="number" className={styles.modalInput} min="1"
                  value={newExp.variant_a_version} onChange={(e) => setNewExp({...newExp, variant_a_version: parseInt(e.target.value) || 1})}
                  style={{ fontFamily: 'inherit' }}
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Variant B (Version)</label>
                <input 
                  type="number" className={styles.modalInput} min="1"
                  value={newExp.variant_b_version} onChange={(e) => setNewExp({...newExp, variant_b_version: parseInt(e.target.value) || 2})}
                  style={{ fontFamily: 'inherit' }}
                />
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 24 }}>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Traffic Split % (A)</label>
                <input 
                  type="number" className={styles.modalInput} min="1" max="99"
                  value={newExp.traffic_split} onChange={(e) => setNewExp({...newExp, traffic_split: parseInt(e.target.value) || 50})}
                  style={{ fontFamily: 'inherit' }}
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Primary Metric</label>
                <select 
                  className={styles.modalInput}
                  value={newExp.primary_metric} onChange={(e) => setNewExp({...newExp, primary_metric: e.target.value})}
                  style={{ fontFamily: 'inherit', appearance: 'auto' }}
                >
                  <option value="cost">Cost (Minimize)</option>
                  <option value="latency">Latency (Minimize)</option>
                  <option value="quality">Quality (Maximize)</option>
                </select>
              </div>
            </div>

            <div className={styles.modalActions}>
              <button className={`${styles.btn} ${styles.btnCancel}`} onClick={() => setIsCreating(false)}>Cancel</button>
              <button className={`${styles.btn} ${styles.btnSave}`} onClick={handleCreate} disabled={!newExp.name || !newExp.prompt_name}>Launch Experiment</button>
            </div>
          </div>
        </div>
      )}

    </DashboardLayout>
  );
}
