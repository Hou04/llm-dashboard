/** Billing page (M10) — replaces loadM10() + generateInvoice() */
'use client';
import { useState, useEffect } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';
import { fmt, fmtK } from '@/lib/utils';
import { useAuth } from '@/lib/auth';

export default function BillingPage() {
  const { selectedTenant } = useDashboard();
  const { user } = useAuth();
  const now = new Date();
  const [tenant, setTenant] = useState(selectedTenant || '');
  const [month, setMonth] = useState(String(now.getFullYear()) + String(now.getMonth() + 1).padStart(2, '0'));
  
  // Invoice State
  const [status, setStatus] = useState('');
  const [invoices, setInvoices] = useState<any[]>([]);
  const [invoice, setInvoice] = useState<any>(null);
  const [lineItems, setLineItems] = useState<any[]>([]);

  // Contract State
  const [contractType, setContractType] = useState('pay_as_you_go');
  const [baseFee, setBaseFee] = useState<number>(0);
  const [includedTokens, setIncludedTokens] = useState<number>(0);
  const [overageRate, setOverageRate] = useState<number>(0);
  const [contractStatus, setContractStatus] = useState('');
  const [contractData, setContractData] = useState<any>(null);
  const [availableTenants, setAvailableTenants] = useState<any[]>([]);

  // Fetch tenants for dropdown
  const fetchTenants = async () => {
    if (user?.role !== 'super_admin') return;
    try {
      const res = await api.get('/v1/tenants');
      setAvailableTenants(res.data || []);
    } catch (e) {
      console.error('Failed to fetch tenants', e);
    }
  };

  const generate = async () => {
    const t = tenant || user?.tenant_id;
    if (!t) { setStatus('⚠ No tenant selected'); return; }
    setStatus('Generating invoice…');
    try {
      const res = await api.post(`/v1/billing/generate/${t}/${month}`);
      setStatus(res.data.success ? `✓ $${fmt(res.data.total_billed_usd)}` : 'Error: ' + (res.data.error || res.data.detail));
      if (res.data.success) {
        viewInvoice(t, month);
        listInvoices();
      }
    } catch (e: any) {
      console.error("[Billing] Generation error:", e);
      if (e.message === 'Network Error' || e.code === 'ECONNABORTED') {
        setStatus('⚠ Network Error: Request timed out. Checking invoices...');
        listInvoices(); // Refresh list anyway, maybe it finished in background
      } else {
        const detail = e.response?.data?.detail || e.response?.data?.error || e.message;
        setStatus('⚠ Generation failed: ' + detail);
      }
    }
  };

  const listInvoices = async () => {
    const t = tenant || user?.tenant_id;
    if (!t) { setStatus('⚠ No tenant selected'); return; }
    try {
      const res = await api.get(`/v1/billing/invoices/${t}`);
      setInvoices(res.data.invoices || []);
      if ((res.data.invoices || []).length === 0) setStatus('No invoices found for ' + t);
      else setStatus('');
    } catch (e: any) { setInvoices([]); setStatus('⚠ Failed to load invoices: ' + (e.response?.data?.detail || e.message)); }
  };

  const viewInvoice = async (t: string, m: string) => {
    const effectiveTenant = t || tenant || user?.tenant_id || '';
    try {
      const res = await api.get(`/v1/billing/invoice/${effectiveTenant}/${m}`);
      setInvoice(res.data.invoice || res.data);
      setLineItems(res.data.line_items || []);
    } catch { setInvoice(null); }
  };

  const finalizeInvoice = async () => {
    if (!invoice) return;
    const t = tenant || user?.tenant_id;
    if (!confirm('Are you sure you want to finalize this invoice? It will be locked from further changes.')) return;
    try {
      await api.post(`/v1/billing/finalize/${t}/${invoice.year_month}`);
      setStatus('✓ Invoice finalized');
      viewInvoice(t || '', invoice.year_month);
      listInvoices();
    } catch (e: any) {
      alert('Failed to finalize: ' + (e.response?.data?.detail || e.message));
    }
  };

  const downloadJson = () => {
    if (!invoice) return;
    const blob = new Blob([JSON.stringify({ invoice, lineItems }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `invoice_${invoice.tenant_id}_${invoice.year_month}.json`;
    a.click();
  };

  const loadContract = async () => {
    const targetTenant = user?.role === 'super_admin' ? tenant : user?.tenant_id;
    if (!targetTenant) return;
    setContractStatus('Loading...');
    try {
      const res = await api.get(`/v1/billing/contract/${targetTenant}`);
      setContractData(res.data);
      setContractType(res.data.contract_type);
      setBaseFee(Number(res.data.base_fee_usd));
      setIncludedTokens(Number(res.data.forfait_tokens));
      setOverageRate(Number(res.data.overage_rate_per_1k));
      setContractStatus('✓ Loaded');
    } catch (e: any) {
      setContractStatus('No contract found / Error');
      setContractData(null);
    }
  };

  const saveContract = async (status: string = 'active') => {
    if (!tenant) return;
    setContractStatus('Saving...');
    try {
      await api.post(`/v1/billing/contract/${tenant}`, {
        contract_type: contractType,
        status: status,
        base_fee_usd: baseFee,
        forfait_tokens: includedTokens,
        overage_rate_per_1k: overageRate,
        description: 'Updated via Admin UI'
      });
      setContractStatus(`✓ Contract saved as ${status}`);
      loadContract();
    } catch (e: any) {
      setContractStatus('Error: ' + e.message);
    }
  };

  const acceptContract = async () => {
    try {
      await api.post(`/v1/billing/contract/${user?.tenant_id}/accept`);
      loadContract();
    } catch (e: any) {
      alert('Failed to accept contract: ' + e.message);
    }
  };

  const rejectContract = async () => {
    try {
      await api.post(`/v1/billing/contract/${user?.tenant_id}/reject`);
      loadContract();
    } catch (e: any) {
      alert('Failed to reject contract: ' + e.message);
    }
  };

  // Replaced broken useState() initializer with proper useEffect
  useEffect(() => {
    fetchTenants();
    if (user?.role !== 'super_admin' && user?.tenant_id) {
      setTenant(user.tenant_id);
      loadContract();
    }
  }, [user?.role, user?.tenant_id]);

  // Sync with global tenant selector
  useEffect(() => {
    if (selectedTenant) {
      setTenant(selectedTenant);
    }
  }, [selectedTenant]);

  const getStatusColor = (s: string) => {
    switch(s) {
      case 'active': return 'var(--green)';
      case 'proposed': return 'var(--yellow)';
      case 'rejected': return 'var(--red)';
      default: return 'var(--t2)';
    }
  };

  return (
    <DashboardLayout title="Billing & Invoices" meta="Invoice generation & contract management">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        
        {/* Left Column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          
          <div className="card">
            <div className="card-header"><div className="card-title">Generate & List Invoices</div></div>
            <div style={{ display: 'grid', gap: 8 }}>
              {user?.role === 'super_admin' ? (
                <select 
                  value={tenant} 
                  onChange={e => { setTenant(e.target.value); setInvoices([]); setInvoice(null); }}
                  style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card2)', color: 'var(--t1)', fontSize: 12 }}
                >
                  <option value="">Select a Tenant</option>
                  {availableTenants.map(t => (
                    <option key={t.tenant_id} value={t.tenant_id}>{t.name} ({t.tenant_id})</option>
                  ))}
                </select>
              ) : (
                <div style={{ fontSize: 12, padding: '6px 8px', background: 'var(--card2)', borderRadius: 'var(--r)', border: '1px solid var(--border2)' }}>
                  Tenant: <span style={{ fontWeight: 600 }}>{user?.tenant_id}</span>
                </div>
              )}
              <input placeholder="YYYYMM" value={month} onChange={e => setMonth(e.target.value)} style={{ padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card2)', color: 'var(--t1)', fontSize: 12 }} />
              <div style={{ display: 'flex', gap: 8 }}>
                {user?.role === 'super_admin' && (
                  <button className="btn btn-primary" onClick={generate} disabled={!tenant} style={{ fontSize: 11 }}>Generate</button>
                )}
                <button className="btn" onClick={listInvoices} disabled={!tenant && !user?.tenant_id} style={{ fontSize: 11 }}>List Invoices</button>
              </div>
              {status && <div style={{ fontSize: 12, color: 'var(--t2)', marginTop: 4 }}>{status}</div>}
            </div>
            {invoices.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>Invoices for {tenant || user?.tenant_id}</div>
                {invoices.map((inv, i) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid var(--border)', fontSize: 12, alignItems: 'center' }}>
                    <div>
                      <div style={{ fontWeight: 600 }}>{inv.year_month}</div>
                      <span style={{ 
                        fontSize: 9, 
                        padding: '1px 5px', 
                        borderRadius: 3, 
                        background: (inv.status === 'finalized' ? 'var(--green)' : 'var(--yellow)') + '15', 
                        color: inv.status === 'finalized' ? 'var(--green)' : 'var(--yellow)',
                        border: `1px solid ${inv.status === 'finalized' ? 'var(--green)' : 'var(--yellow)'}33`,
                        fontWeight: 700,
                        textTransform: 'uppercase'
                      }}>
                        {inv.status || 'draft'}
                      </span>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontWeight: 700 }}>${fmt(inv.total_billed_usd)}</div>
                      <button className="btn" style={{ fontSize: 10, padding: '2px 10px', marginTop: 4 }} onClick={() => viewInvoice(tenant || user?.tenant_id || '', inv.year_month)}>Details</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div className="card-title">Contract Settings</div>
                {contractData && (
                  <span style={{ 
                    fontSize: 9, 
                    padding: '2px 6px', 
                    borderRadius: 4, 
                    background: getStatusColor(contractData.status) + '22', 
                    color: getStatusColor(contractData.status),
                    border: `1px solid ${getStatusColor(contractData.status)}44`,
                    textTransform: 'uppercase',
                    fontWeight: 700
                  }}>
                    {contractData.status}
                  </span>
                )}
              </div>
              {user?.role === 'super_admin' && (
                <button className="btn" onClick={loadContract} style={{ fontSize: 10, padding: '2px 8px' }}>Load</button>
              )}
            </div>

            {user?.role === 'super_admin' ? (
              <div style={{ display: 'grid', gap: 12, fontSize: 12 }}>
                <div>
                  <div style={{ marginBottom: 4, color: 'var(--t2)', fontSize: 10, textTransform: 'uppercase' }}>Contract Type</div>
                  <select value={contractType} onChange={e => setContractType(e.target.value)} style={{ width: '100%', padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card2)', color: 'var(--t1)' }}>
                    <option value="pay_as_you_go">Pay As You Go</option>
                    <option value="forfait">Forfait (Fixed base + allowance)</option>
                    <option value="hybrid">Hybrid</option>
                  </select>
                </div>
                
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <div>
                    <div style={{ marginBottom: 4, color: 'var(--t2)', fontSize: 10, textTransform: 'uppercase' }}>Base Fee (USD)</div>
                    <input type="number" value={baseFee} onChange={e => setBaseFee(Number(e.target.value))} style={{ width: '100%', padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card2)', color: 'var(--t1)' }} />
                  </div>
                  <div>
                    <div style={{ marginBottom: 4, color: 'var(--t2)', fontSize: 10, textTransform: 'uppercase' }}>Included Tokens</div>
                    <input type="number" value={includedTokens} onChange={e => setIncludedTokens(Number(e.target.value))} style={{ width: '100%', padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card2)', color: 'var(--t1)' }} />
                  </div>
                </div>

                <div>
                  <div style={{ marginBottom: 4, color: 'var(--t2)', fontSize: 10, textTransform: 'uppercase' }}>Overage Rate per 1k Tokens (USD)</div>
                  <input type="number" step="0.001" value={overageRate} onChange={e => setOverageRate(Number(e.target.value))} style={{ width: '100%', padding: '6px 8px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card2)', color: 'var(--t1)' }} />
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
                  <div style={{ color: contractStatus.includes('Error') ? 'var(--red)' : 'var(--green)', fontSize: 11 }}>{contractStatus}</div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn" onClick={() => saveContract('proposed')} style={{ fontSize: 11 }}>Propose</button>
                    <button className="btn btn-primary" onClick={() => saveContract('active')} style={{ fontSize: 11 }}>Save Active</button>
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ fontSize: 12 }}>
                {contractData ? (
                  <div style={{ display: 'grid', gap: 12 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                      <div className="kpi" style={{ padding: '8px 12px' }}>
                        <div className="kpi-label">Type</div>
                        <div className="kpi-value" style={{ fontSize: 14 }}>{contractData.contract_type}</div>
                      </div>
                      <div className="kpi" style={{ padding: '8px 12px' }}>
                        <div className="kpi-label">Base Fee</div>
                        <div className="kpi-value" style={{ fontSize: 14 }}>${fmt(contractData.base_fee_usd)}</div>
                      </div>
                    </div>
                    {contractData.status === 'proposed' && (
                      <div style={{ padding: 12, background: 'var(--yellow)11', border: '1px solid var(--yellow)33', borderRadius: 'var(--r)', marginTop: 8 }}>
                        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: 'var(--yellow)' }}>A new contract has been proposed</div>
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button className="btn btn-primary" onClick={acceptContract} style={{ flex: 1, fontSize: 11 }}>Accept Contract</button>
                          <button className="btn" onClick={rejectContract} style={{ flex: 1, fontSize: 11, color: 'var(--red)' }}>Reject</button>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div style={{ color: 'var(--t2)', textAlign: 'center', padding: '20px 0' }}>No active contract terms found.</div>
                )}
              </div>
            )}
          </div>

        </div>

        {/* Right Column */}
        <div>
          {invoice && (
            <div className="card">
              <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div className="card-title">Invoice Detail</div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="btn" onClick={downloadJson} style={{ fontSize: 10, padding: '2px 8px' }}>JSON</button>
                  {user?.role === 'super_admin' && invoice.status !== 'finalized' && (
                    <button className="btn btn-primary" onClick={finalizeInvoice} style={{ fontSize: 10, padding: '2px 8px' }}>Finalize</button>
                  )}
                </div>
              </div>
              <div className="kpi-grid-2" style={{ marginBottom: 14 }}>
                <div className="kpi" style={{ padding: '10px 12px' }}><div className="kpi-label">Total</div><div className="kpi-value" style={{ fontSize: 20 }}>${fmt(invoice.total_billed_usd)}</div></div>
                <div className="kpi" style={{ padding: '10px 12px' }}>
                  <div className="kpi-label">Status</div>
                  <div className="kpi-value" style={{ 
                    fontSize: 16, 
                    color: invoice.status === 'finalized' ? 'var(--green)' : 'var(--yellow)',
                    fontWeight: 700
                  }}>
                    {invoice.status?.toUpperCase() || 'DRAFT'}
                  </div>
                </div>
              </div>
              {lineItems.length > 0 && (
                <table><thead><tr><th style={{ textAlign: 'left' }}>Description</th><th style={{ textAlign: 'right' }}>Qty</th><th style={{ textAlign: 'right' }}>Unit</th><th style={{ textAlign: 'right' }}>Total</th></tr></thead>
                  <tbody>{lineItems.map((l, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '8px 0', fontSize: 12 }}>{l.description || l.line_type}</td>
                      <td style={{ padding: '8px 0', fontSize: 12, textAlign: 'right' }}>{fmtK(l.quantity || 0)}</td>
                      <td style={{ padding: '8px 0', fontSize: 12, textAlign: 'right' }}>${parseFloat(l.unit_price_usd || 0).toFixed(6)}</td>
                      <td style={{ padding: '8px 0', fontSize: 12, textAlign: 'right', fontWeight: 600 }}>${fmt(l.line_total_usd)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    </DashboardLayout>
  );
}
