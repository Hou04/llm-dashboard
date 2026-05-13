/**
 * AI Infrastructure & Gateway Keys
 * 
 * Replaces the old "Model Catalog" with a dev-ops focused view.
 * Tab 1: Service Health (Provider uptime & latency)
 * Tab 2: Gateway Keys (Virtual keys to access the gateway)
 */

'use client';

import { useState, useEffect, useCallback } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { IconSearch, IconChart, IconKey, IconPlus, IconX, IconCheck, IconTrash, IconActivity } from '@/components/Icons';
import { useDashboard } from '@/lib/dashboard-context';
import { useAuth } from '@/lib/auth';
import api from '@/lib/api';
import styles from './Models.module.css';

// Fake provider health data (in a real app, this would come from a /health endpoint)
const getProviderInfo = (name: string) => {
  const map: any = {
    'openai': { icon: '🤖', color: '#10a37f' },
    'anthropic': { icon: 'A\\', color: '#d9730d' },
    'google': { icon: 'G', color: '#4285f4' },
    'meta': { icon: 'M', color: '#0668E1' },
    'mistral': { icon: 'M', color: '#f59e0b' },
    'groq': { icon: 'Gq', color: '#f97316' },
    'cohere': { icon: 'C', color: '#14b8a6' },
  };
  return map[name.toLowerCase()] || { icon: '☁️', color: '#ffffff' };
};

export default function InfrastructurePage() {
  const { user } = useAuth();
  const { selectedTenant, setSelectedTenant } = useDashboard();
  const [tenants, setTenants] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState('health');
  
  // Virtual Keys State
  const [keys, setKeys] = useState<any[]>([]);
  const [loadingKeys, setLoadingKeys] = useState(false);
  
  // Health State
  const [providers, setProviders] = useState<any[]>([]);
  const [loadingHealth, setLoadingHealth] = useState(false);

  // Create Key Modal State
  const [isCreatingKey, setIsCreatingKey] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyEnv, setNewKeyEnv] = useState('live');
  const [newKeyBudget, setNewKeyBudget] = useState<number | ''>('');
  
  // Display newly created key
  const [createdRawKey, setCreatedRawKey] = useState<string | null>(null);

  const loadHealth = useCallback(async () => {
    setLoadingHealth(true);
    try {
      const res = await api.get('/v1/gateway/health/providers');
      setProviders(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingHealth(false);
    }
  }, []);

  const loadKeys = useCallback(async () => {
    if (!selectedTenant) return;
    setLoadingKeys(true);
    try {
      // Pass tenant_id to filter results, especially for super_admin
      const res = await api.get(`/v1/auth/virtual-keys?tenant_id=${selectedTenant}`);
      setKeys(res.data.keys || []);
    } catch (err: any) {
      console.error("[Models] Failed to load keys:", err);
      if (err.response?.status === 403) {
        setKeys([]); // Clear keys if forbidden
      }
    } finally {
      setLoadingKeys(false);
    }
  }, [selectedTenant]);

  useEffect(() => {
    if (activeTab === 'keys') {
      loadKeys();
    } else {
      loadHealth();
    }
  }, [activeTab, loadKeys, loadHealth]);

  useEffect(() => {
    if (user?.role === 'super_admin') {
      api.get('/v1/tenants').then(r => setTenants(r.data.map((t: any) => t.tenant_id))).catch(() => {});
    }
  }, [user]);

  const handleCreateKey = async () => {
    try {
      const payload = {
        name: newKeyName,
        environment: newKeyEnv,
        budget_usd: newKeyBudget || null,
        tenant_id: selectedTenant
      };
      const res = await api.post('/v1/auth/virtual-keys', payload);
      setCreatedRawKey(res.data.raw_key);
      setIsCreatingKey(false);
      setNewKeyName('');
      setNewKeyBudget('');
      loadKeys();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to create key");
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    if (!confirm("Are you sure you want to revoke this key? It will stop working immediately.")) return;
    try {
      await api.delete(`/v1/auth/virtual-keys/${keyId}`);
      loadKeys();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to revoke key");
    }
  };

  return (
    <DashboardLayout title="AI Infrastructure" meta="Manage service health and gateway access">
      <div className={styles.container}>
        
        {/* Navigation Tabs */}
        <div className={styles.tabs}>
          <div 
            className={`${styles.tab} ${activeTab === 'health' ? styles.tabActive : ''}`}
            onClick={() => setActiveTab('health')}
          >
            <IconChart size={16} /> Service Health
          </div>
          <div 
            className={`${styles.tab} ${activeTab === 'keys' ? styles.tabActive : ''}`}
            onClick={() => setActiveTab('keys')}
          >
            <IconKey size={16} /> Gateway Keys
          </div>
        </div>

        {/* --- TAB: HEALTH --- */}
        {activeTab === 'health' && (
          <div className={styles.grid}>
            {loadingHealth && <div style={{ color: 'var(--t3)', padding: 20 }}>Loading real-time health data...</div>}
            {!loadingHealth && providers.map(p => {
              const info = getProviderInfo(p.provider);
              return (
                <div key={p.provider} className={styles.card} style={{ cursor: 'default' }}>
                  <div className={styles.cardHeader}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <div 
                        className={styles.logoBox} 
                        style={{ color: info.color, boxShadow: `inset 0 0 20px ${info.color}15`, fontSize: info.icon.length > 2 ? '14px' : '24px', fontWeight: 800 }}
                      >
                        {info.icon}
                      </div>
                      <div>
                        <div className={styles.cardTitle} style={{ marginBottom: 2, textTransform: 'capitalize' }}>{p.provider}</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--t3)' }}>
                          <span style={{ 
                            width: 8, height: 8, borderRadius: '50%', 
                            background: p.status === 'online' ? 'var(--green)' : 'var(--orange)' 
                          }} />
                          {p.status === 'online' ? 'Operational' : 'Degraded Performance'}
                        </div>
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 16px', background: 'var(--bg2)', borderRadius: 'var(--r1)' }}>
                    <div>
                      <div style={{ fontSize: 10, color: 'var(--t3)', textTransform: 'uppercase', marginBottom: 4 }}>Uptime (30d)</div>
                      <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)' }}>{p.uptime}%</div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 10, color: 'var(--t3)', textTransform: 'uppercase', marginBottom: 4 }}>Avg Latency</div>
                      <div style={{ fontSize: 14, fontWeight: 700, color: (p.latency && p.latency > 500) ? 'var(--orange)' : 'var(--t1)' }}>
                        {p.latency !== null ? `${p.latency} ms` : 'N/A'}
                      </div>
                    </div>
                  </div>
                  <div style={{ marginTop: 12 }}>
                    <button 
                      className={styles.btnSmall} 
                      onClick={(e) => {
                        e.stopPropagation();
                        alert(`Triggering real-time heartbeat check for ${p.provider}...`);
                      }}
                      style={{ width: '100%', fontSize: 11, padding: '6px' }}
                    >
                      <IconActivity size={12} /> Test Connection
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* --- TAB: GATEWAY KEYS --- */}
        {activeTab === 'keys' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ fontSize: 16, margin: '0 0 4px', color: 'var(--t1)' }}>API Access Keys</h3>
                <p style={{ fontSize: 13, color: 'var(--t3)', margin: 0 }}>Create keys to allow your applications to call the AI Gateway.</p>
              </div>
              <button className={`${styles.btn} ${styles.btnSave}`} onClick={() => setIsCreatingKey(true)} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <IconPlus size={14} /> Create Key
              </button>
            </div>

            {createdRawKey && (
              <div style={{ padding: 20, background: 'rgba(16, 163, 127, 0.1)', border: '1px solid var(--green)', borderRadius: 'var(--r2)' }}>
                <h4 style={{ color: 'var(--green)', margin: '0 0 8px', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <IconCheck size={16} /> Key Created Successfully!
                </h4>
                <p style={{ fontSize: 13, color: 'var(--t2)', margin: '0 0 12px' }}>Please copy this key now. You will not be able to see it again.</p>
                <div style={{ padding: 12, background: 'var(--bg)', borderRadius: 'var(--r1)', fontFamily: 'monospace', fontSize: 14, color: 'var(--t1)', border: '1px solid var(--border)', userSelect: 'all' }}>
                  {createdRawKey}
                </div>
                <button className={styles.btn} onClick={() => setCreatedRawKey(null)} style={{ marginTop: 12 }}>Close</button>
              </div>
            )}

            <div className={styles.card} style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg2)' }}>
                    <th style={{ padding: '12px 20px', fontSize: 12, color: 'var(--t3)', fontWeight: 600 }}>NAME</th>
                    <th style={{ padding: '12px 20px', fontSize: 12, color: 'var(--t3)', fontWeight: 600 }}>KEY PREFIX</th>
                    <th style={{ padding: '12px 20px', fontSize: 12, color: 'var(--t3)', fontWeight: 600 }}>ENVIRONMENT</th>
                    <th style={{ padding: '12px 20px', fontSize: 12, color: 'var(--t3)', fontWeight: 600 }}>BUDGET</th>
                    <th style={{ padding: '12px 20px', fontSize: 12, color: 'var(--t3)', fontWeight: 600 }}>STATUS</th>
                    <th style={{ padding: '12px 20px', fontSize: 12, color: 'var(--t3)', fontWeight: 600 }}>ACTIONS</th>
                  </tr>
                </thead>
                <tbody>
                  {loadingKeys ? (
                    <tr><td colSpan={6} style={{ padding: 40, textAlign: 'center', color: 'var(--t3)' }}>Loading...</td></tr>
                  ) : keys.length === 0 ? (
                    <tr><td colSpan={6} style={{ padding: 40, textAlign: 'center', color: 'var(--t3)' }}>No keys created yet.</td></tr>
                  ) : keys.map((k) => (
                    <tr key={k.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '16px 20px', fontSize: 13, color: 'var(--t1)', fontWeight: 500 }}>{k.name}</td>
                      <td style={{ padding: '16px 20px', fontSize: 13, fontFamily: 'monospace', color: 'var(--t2)' }}>{k.key_prefix}...</td>
                      <td style={{ padding: '16px 20px' }}>
                        <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 10, background: k.environment === 'live' ? 'rgba(var(--blue-rgb), 0.1)' : 'var(--bg2)', color: k.environment === 'live' ? 'var(--blue)' : 'var(--t3)', textTransform: 'uppercase', fontWeight: 600 }}>
                          {k.environment}
                        </span>
                      </td>
                      <td style={{ padding: '16px 20px', fontSize: 13, color: 'var(--t2)' }}>
                        {k.budget_usd ? `$${k.budget_used_usd.toFixed(2)} / $${k.budget_usd}` : 'Unlimited'}
                      </td>
                      <td style={{ padding: '16px 20px', fontSize: 13, color: k.is_active ? 'var(--green)' : 'var(--red)' }}>
                        {k.is_active ? 'Active' : 'Revoked'}
                      </td>
                      <td style={{ padding: '16px 20px' }}>
                        {k.is_active && (
                          <button 
                            style={{ background: 'transparent', border: 'none', color: 'var(--red)', cursor: 'pointer' }}
                            onClick={() => handleRevokeKey(k.id)}
                            title="Revoke Key"
                          >
                            <IconX size={16} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Create Key Modal */}
      {isCreatingKey && (
        <div className={styles.modalOverlay} onClick={() => setIsCreatingKey(false)}>
          <div className={styles.modal} onClick={e => e.stopPropagation()}>
            <div className={styles.modalTitle}>
              Create Gateway Key
            </div>
            
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Key Name</label>
              <input 
                type="text"
                className={styles.modalInput}
                placeholder="e.g. Marketing Dept Bot"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                autoFocus
                style={{ fontFamily: 'inherit' }}
              />
            </div>

            {user?.role === 'super_admin' && (
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Target Tenant</label>
                <select 
                  className={styles.modalInput}
                  value={selectedTenant || ''}
                  onChange={(e) => setSelectedTenant(e.target.value)}
                  style={{ fontFamily: 'inherit', appearance: 'auto' }}
                >
                  <option value="">-- Select Tenant --</option>
                  {tenants.map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
            )}

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Environment</label>
              <select 
                className={styles.modalInput}
                value={newKeyEnv}
                onChange={(e) => setNewKeyEnv(e.target.value)}
                style={{ fontFamily: 'inherit', appearance: 'auto' }}
              >
                <option value="live">Live (Production)</option>
                <option value="test">Test (Sandbox)</option>
              </select>
            </div>

            <div style={{ marginBottom: 24 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t3)', marginBottom: 8, textTransform: 'uppercase' }}>Monthly Budget (USD) - Optional</label>
              <input 
                type="number"
                className={styles.modalInput}
                placeholder="Unlimited"
                value={newKeyBudget}
                onChange={(e) => setNewKeyBudget(e.target.value ? Number(e.target.value) : '')}
                style={{ fontFamily: 'inherit' }}
              />
            </div>

            <div className={styles.modalActions}>
              <button className={`${styles.btn} ${styles.btnCancel}`} onClick={() => setIsCreatingKey(false)}>
                Cancel
              </button>
              <button 
                className={`${styles.btn} ${styles.btnSave}`} 
                onClick={handleCreateKey} 
                disabled={!newKeyName || (user?.role === 'super_admin' && !selectedTenant)}
              >
                Generate Key
              </button>
            </div>
          </div>
        </div>
      )}

    </DashboardLayout>
  );
}
