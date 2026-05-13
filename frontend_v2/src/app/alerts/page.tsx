/** Alert Settings page — NEW: Phase 3 webhook configuration */
'use client';
import { useEffect, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';

export default function AlertsPage() {
  const { selectedTenant } = useDashboard();
  const [configs, setConfigs] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: '', channel_type: 'slack', webhook_url: '' });

  const load = async () => {
    setLoading(true);
    try {
      const params = selectedTenant ? `?tenant_id=${selectedTenant}` : '';
      const [cfgR, histR] = await Promise.all([
        api.get(`/v1/tracing/alerts/configs${params}`).catch(() => ({ data: [] })),
        api.get(`/v1/tracing/alerts/history${params}`).catch(() => ({ data: [] })),
      ]);
      setConfigs(cfgR.data.configs || []);
      setHistory(histR.data.alerts || []);
    } catch {} finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [selectedTenant]);

  const create = async () => {
    try {
      await api.post('/v1/tracing/alerts/configs', { ...form, tenant_id: selectedTenant || undefined });
      setShowForm(false); setForm({ name: '', channel_type: 'slack', webhook_url: '' }); load();
    } catch (e: any) { alert(e.message); }
  };

  const testWebhook = async (id: string) => {
    try { await api.post(`/v1/tracing/alerts/configs/${id}/test`); alert('Test sent!'); }
    catch (e: any) { alert(e.message); }
  };

  const deleteConfig = async (id: string) => {
    if (!confirm('Remove this channel?')) return;
    try { await api.delete(`/v1/tracing/alerts/configs/${id}`); load(); }
    catch (e: any) { alert(e.message); }
  };

  return (
    <DashboardLayout title="Alert Settings" meta="Webhook channels & notification history">
      {loading && <div className="loading"><div className="spinner" /> Loading…</div>}
      {!loading && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Webhook Channels</div>
              <button className="btn btn-primary" style={{ fontSize: 11 }} onClick={() => setShowForm(!showForm)}>+ Add Channel</button>
            </div>
            {showForm && (
              <div style={{ padding: 12, marginBottom: 12, background: 'var(--card2)', borderRadius: 'var(--r)', display: 'grid', gap: 8 }}>
                <input placeholder="Channel name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card)', color: 'var(--t1)', fontSize: 12 }} />
                <select value={form.channel_type} onChange={e => setForm({ ...form, channel_type: e.target.value })} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card)', color: 'var(--t1)', fontSize: 12 }}>
                  <option value="slack">Slack</option><option value="teams">Teams</option><option value="pagerduty">PagerDuty</option><option value="generic">Generic Webhook</option><option value="email">Email</option>
                </select>
                <input placeholder="Webhook URL" value={form.webhook_url} onChange={e => setForm({ ...form, webhook_url: e.target.value })} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card)', color: 'var(--t1)', fontSize: 12 }} />
                <button className="btn btn-primary" onClick={create} style={{ fontSize: 11 }}>Create</button>
              </div>
            )}
            {configs.length === 0 ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>No channels configured</div> :
              configs.map((c, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>{c.name}</div>
                    <div style={{ fontSize: 10, color: 'var(--t2)' }}>{c.channel_type} · {c.total_alerts_sent || 0} sent</div>
                  </div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn" style={{ fontSize: 10, padding: '2px 6px' }} onClick={() => testWebhook(c.id)}>Test</button>
                    <button className="btn btn-danger" style={{ fontSize: 10, padding: '2px 6px' }} onClick={() => deleteConfig(c.id)}>×</button>
                  </div>
                </div>
              ))
            }
          </div>

          <div className="card">
            <div className="card-header"><div className="card-title">Alert History</div></div>
            {history.length === 0 ? <div style={{ padding: 16, fontSize: 12, color: 'var(--t2)' }}>No alerts dispatched yet</div> :
              <table><thead><tr><th>Type</th><th>Channel</th><th>Status</th><th>Sent</th></tr></thead>
                <tbody>{history.slice(0, 20).map((h, i) => (
                  <tr key={i}>
                    <td>{h.alert_type}</td>
                    <td>{h.channel_type}</td>
                    <td>{h.success ? <span className="badge badge-ok">✓</span> : <span className="badge badge-critical">✗</span>}</td>
                    <td style={{ fontSize: 10, color: 'var(--t3)' }}>{h.sent_at ? new Date(h.sent_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}</tbody>
              </table>
            }
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}
