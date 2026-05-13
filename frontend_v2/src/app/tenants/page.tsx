/** Tenants management page (Infrastructure Cockpit) */
'use client';
import { useState, useEffect } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import api from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useRouter } from 'next/navigation';

export default function TenantsPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [tenants, setTenants] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (user && user.role !== 'super_admin') {
      router.push('/');
      return;
    }
    fetchTenants();
  }, [user]);

  const fetchTenants = async () => {
    setLoading(true);
    try {
      // Fetch tenants and their contracts in parallel or sequence
      const res = await api.get('/v1/tenants');
      const tenantList = res.data || [];
      
      // For each tenant, try to get their contract status
      const augmentedTenants = await Promise.all(tenantList.map(async (t: any) => {
        try {
          const cRes = await api.get(`/v1/billing/contract/${t.tenant_id}`);
          return { ...t, contract: cRes.data };
        } catch {
          return { ...t, contract: null };
        }
      }));
      
      setTenants(augmentedTenants);
    } catch (e) {
      console.error('Failed to fetch tenants', e);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (s: string) => {
    switch(s) {
      case 'active': return 'var(--green)';
      case 'proposed': return 'var(--yellow)';
      case 'rejected': return 'var(--red)';
      case 'draft': return 'var(--t3)';
      default: return 'var(--t2)';
    }
  };

  return (
    <DashboardLayout title="Tenant Infrastructure" meta="Organization management & contract oversight">
      <div className="card">
        <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="card-title">All Organizations</div>
          <button className="btn" onClick={fetchTenants} style={{ fontSize: 11 }}>Refresh</button>
        </div>
        
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--t2)' }}>Loading infrastructure data...</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border2)', color: 'var(--t2)', fontSize: 11, textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '12px 8px' }}>Organization</th>
                <th style={{ textAlign: 'left', padding: '12px 8px' }}>ID</th>
                <th style={{ textAlign: 'left', padding: '12px 8px' }}>Billing Tier</th>
                <th style={{ textAlign: 'center', padding: '12px 8px' }}>Contract Status</th>
                <th style={{ textAlign: 'right', padding: '12px 8px' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.tenant_id} style={{ borderBottom: '1px solid var(--border)', fontSize: 13 }}>
                  <td style={{ padding: '14px 8px' }}>
                    <div style={{ fontWeight: 600 }}>{t.name}</div>
                    <div style={{ fontSize: 10, color: 'var(--t2)' }}>{t.contact_email}</div>
                  </td>
                  <td style={{ padding: '14px 8px', color: 'var(--t2)', fontFamily: 'monospace', fontSize: 11 }}>{t.tenant_id}</td>
                  <td style={{ padding: '14px 8px' }}>
                    <span style={{ fontSize: 11, background: 'var(--card2)', padding: '2px 8px', borderRadius: 10, border: '1px solid var(--border2)' }}>
                      {t.contract?.contract_type || t.tier || 'pay_as_you_go'}
                    </span>
                  </td>
                  <td style={{ padding: '14px 8px', textAlign: 'center' }}>
                    {t.contract ? (
                      <span style={{ 
                        fontSize: 10, 
                        padding: '3px 8px', 
                        borderRadius: 6, 
                        background: getStatusColor(t.contract.status) + '11', 
                        color: getStatusColor(t.contract.status),
                        border: `1px solid ${getStatusColor(t.contract.status)}33`,
                        fontWeight: 600
                      }}>
                        {t.contract.status.toUpperCase()}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--t3)', fontSize: 11 }}>No Contract</span>
                    )}
                  </td>
                  <td style={{ padding: '14px 8px', textAlign: 'right' }}>
                    <button 
                      className="btn" 
                      style={{ fontSize: 11, padding: '4px 10px' }}
                      onClick={() => router.push(`/billing?tenant=${t.tenant_id}`)}
                    >
                      Manage Billing
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </DashboardLayout>
  );
}
