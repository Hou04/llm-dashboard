/** Governance Rules page (M7) — replaces loadM7() */
'use client';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import { useAuth } from '@/lib/auth';
import api from '@/lib/api';
function fmtK(n: number | string) { const v = parseFloat(String(n || 0)); return v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1000 ? (v / 1000).toFixed(0) + 'K' : String(Math.round(v)); }

export default function GovernancePage() {
  const { selectedTenant, period } = useDashboard();
  const { isAdmin } = useAuth();
  const [rules, setRules] = useState<any[]>([]);
  const [summary, setSummary] = useState<any>({});
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ rule_type: 'rate_limit', tenant_id: '', model_name: '', limit_value: '', description: '' });
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const [sumR, rulesR] = await Promise.all([
        api.get(`/v1/gateway/governance/summary?period_days=${period}`).catch(() => ({ data: {} })),
        api.get('/v1/gateway/governance/rules' + (selectedTenant ? '?tenant_id=' + selectedTenant : '')).catch(() => ({ data: { rules: [] } })),
      ]);
      setSummary(sumR.data);
      setRules(rulesR.data.rules || []);
    } catch {} finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [selectedTenant, period]);

  const createRule = async () => {
    const body: any = { rule_type: form.rule_type, description: form.description || 'Created from dashboard', is_active: true };
    if (form.tenant_id) body.tenant_id = form.tenant_id;
    if (form.model_name) body.model_name = form.model_name;
    if (form.limit_value) body.limit_value = parseFloat(form.limit_value);
    try { await api.post('/v1/gateway/governance/rules', body); setShowForm(false); load(); } catch (e: any) { alert(e.message); }
  };

  const deactivate = async (id: string) => {
    if (!confirm('Deactivate this rule?')) return;
    try { await api.delete(`/v1/gateway/governance/rules/${id}`); load(); } catch (e: any) { alert(e.message); }
  };

  return (
    <DashboardLayout title="Governance Rules" meta="Rate limits, quotas & cost controls">
      {loading && <div className="loading"><div className="spinner" /> Loading…</div>}
      {!loading && (
        <>
          <div className="kpi-grid-4">
            <div className="kpi"><div className="kpi-label">Blocked Calls</div><div className="kpi-value">{fmtK(summary.blocked_calls || 0)}</div></div>
            <div className="kpi"><div className="kpi-label">Downgraded</div><div className="kpi-value">{fmtK(summary.downgraded_calls || 0)}</div></div>
            <div className="kpi"><div className="kpi-label">Active Rules</div><div className="kpi-value">{rules.length}</div></div>
            <div className="kpi"><div className="kpi-label">Cost Saved</div><div className="kpi-value">${parseFloat(summary.estimated_cost_saved_usd || 0).toFixed(2)}</div></div>
          </div>

          <div className="card">
            <div className="card-header">
              <div className="card-title">Rules</div>
              {isAdmin() && <button className="btn btn-primary" style={{ fontSize: 11 }} onClick={() => setShowForm(!showForm)}>+ New Rule</button>}
            </div>

            {showForm && (
              <div style={{ padding: 12, marginBottom: 12, background: 'var(--card2)', borderRadius: 'var(--r)', display: 'grid', gap: 8 }}>
                <select value={form.rule_type} onChange={e => setForm({ ...form, rule_type: e.target.value })} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card)', color: 'var(--t1)', fontSize: 12 }}>
                  <option value="rate_limit">Rate Limit</option><option value="cost_limit">Cost Limit</option><option value="token_limit">Token Limit</option><option value="model_block">Model Block</option>
                </select>
                <input placeholder="Tenant (optional)" value={form.tenant_id} onChange={e => setForm({ ...form, tenant_id: e.target.value })} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card)', color: 'var(--t1)', fontSize: 12 }} />
                <input placeholder="Model (optional)" value={form.model_name} onChange={e => setForm({ ...form, model_name: e.target.value })} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card)', color: 'var(--t1)', fontSize: 12 }} />
                <input placeholder="Limit value" type="number" value={form.limit_value} onChange={e => setForm({ ...form, limit_value: e.target.value })} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card)', color: 'var(--t1)', fontSize: 12 }} />
                <button className="btn btn-primary" onClick={createRule} style={{ fontSize: 11 }}>Create Rule</button>
              </div>
            )}

            {rules.length === 0 ? <div style={{ padding: 12, fontSize: 12, color: 'var(--t2)' }}>No rules configured</div> :
              <table><thead><tr><th>Tenant</th><th>Type</th><th>Model</th><th>Limit</th><th>Status</th>{isAdmin() && <th>Action</th>}</tr></thead>
                <tbody>{rules.map((r, i) => (
                  <tr key={i}>
                    <td>{r.tenant_id || 'global'}</td>
                    <td><span className="badge badge-blue">{r.rule_type}</span></td>
                    <td style={{ color: 'var(--t2)' }}>{r.model_name || '—'}</td>
                    <td>{r.limit_value ?? '—'}</td>
                    <td>{r.is_active ? <span className="badge badge-ok">active</span> : <span className="badge badge-info">—</span>}</td>
                    {isAdmin() && <td><button className="btn" style={{ fontSize: 10, padding: '2px 6px' }} onClick={() => deactivate(r.id)}>Deactivate</button></td>}
                  </tr>
                ))}</tbody>
              </table>
            }
          </div>
        </>
      )}
    </DashboardLayout>
  );
}
