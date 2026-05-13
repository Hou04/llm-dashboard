/**
 * Professional Settings Hub — Big Company Style
 * Split into categories: General, Team, Security, Billing
 */

'use client';

import { useState, useEffect } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { 
  IconGear, 
  IconUsers, 
  IconShield, 
  IconCost, 
  IconBuilding, 
  IconCheck, 
  IconX 
} from '@/components/Icons';
import { useAuth } from '@/lib/auth';
import api from '@/lib/api';
import styles from './page.module.css';

export default function SettingsPage() {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState('general');
  const [members, setMembers] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  // General Settings State
  const [wsName, setWsName] = useState('Acme Corp Infrastructure');
  const [isSaved, setIsSaved] = useState(false);

  useEffect(() => {
    if (activeTab === 'team') {
      fetchTeam();
    }
  }, [activeTab]);

  const fetchTeam = async () => {
    setLoading(true);
    try {
      // In a real app, this would be filtered by tenant_id on the server
      const res = await api.get('/v1/auth/users');
      // Filter for current tenant members
      const tenantMembers = (res.data.users || []).filter((u: any) => u.tenant_id === user?.tenant_id);
      setMembers(tenantMembers);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveGeneral = () => {
    setIsSaved(true);
    setTimeout(() => setIsSaved(false), 2000);
  };

  if (!user) return null;

  return (
    <DashboardLayout title="Settings" meta="Manage your organization and security">
      <div className={styles.container}>
        
        {/* Settings Sidebar */}
        <div className={styles.sidebar}>
          <div 
            className={`${styles.navItem} ${activeTab === 'general' ? styles.navItemActive : ''}`}
            onClick={() => setActiveTab('general')}
          >
            <IconBuilding size={18} /> General
          </div>
          <div 
            className={`${styles.navItem} ${activeTab === 'team' ? styles.navItemActive : ''}`}
            onClick={() => setActiveTab('team')}
          >
            <IconUsers size={18} /> Team Members
          </div>
          <div 
            className={`${styles.navItem} ${activeTab === 'security' ? styles.navItemActive : ''}`}
            onClick={() => setActiveTab('security')}
          >
            <IconShield size={18} /> Security & Auth
          </div>
          <div 
            className={`${styles.navItem} ${activeTab === 'billing' ? styles.navItemActive : ''}`}
            onClick={() => setActiveTab('billing')}
          >
            <IconCost size={18} /> Billing & Plans
          </div>
        </div>

        {/* Content Area */}
        <div className={styles.content}>
          
          {/* SECTION: GENERAL */}
          {activeTab === 'general' && (
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.title}>General Settings</h2>
                <p className={styles.subtitle}>Manage your workspace identity and basic configuration.</p>
              </div>

              <div className={styles.card}>
                <div className={styles.formGroup}>
                  <label className={styles.label}>Workspace Name</label>
                  <input 
                    type="text" 
                    className={styles.input} 
                    value={wsName} 
                    onChange={(e) => setWsName(e.target.value)} 
                  />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.label}>Tenant ID (Immutable)</label>
                  <input 
                    type="text" 
                    className={`${styles.input} styles.inputReadOnly`} 
                    value={user.tenant_id || 'Global'} 
                    readOnly 
                  />
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <button className={`${styles.btn} ${styles.btnPrimary}`} onClick={handleSaveGeneral}>
                    {isSaved ? '✓ Changes Saved' : 'Save Changes'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* SECTION: TEAM */}
          {activeTab === 'team' && (
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.title}>Team Members</h2>
                <p className={styles.subtitle}>Manage who has access to this workspace.</p>
              </div>

              <div className={styles.card} style={{ padding: 0, overflow: 'hidden' }}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Role</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading ? (
                      <tr><td colSpan={3} style={{ textAlign: 'center', padding: 40 }}>Loading team...</td></tr>
                    ) : (
                      members.map((m: any) => (
                        <tr key={m.id}>
                          <td>
                            <div style={{ fontWeight: 600 }}>{m.username}</div>
                            <div style={{ fontSize: 12, color: 'var(--t3)' }}>{m.email || 'No email provided'}</div>
                          </td>
                          <td>
                            <span className={`${styles.badge} ${m.role === 'tenant_admin' ? styles.badgeAdmin : styles.badgeUser}`}>
                              {m.role === 'tenant_admin' ? 'Admin' : 'Staff'}
                            </span>
                          </td>
                          <td style={{ color: 'var(--green)', fontWeight: 600 }}>Active</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
                <div style={{ padding: 16, textAlign: 'center', borderTop: '1px solid var(--border)' }}>
                   <button className={styles.btn} style={{ color: 'var(--acc)', fontWeight: 600 }}>+ Invite New Member</button>
                </div>
              </div>
            </div>
          )}

          {/* SECTION: SECURITY */}
          {activeTab === 'security' && (
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.title}>Security & Auth</h2>
                <p className={styles.subtitle}>Manage your account security and authentication methods.</p>
              </div>

              <div className={styles.card}>
                <h3 className={styles.cardTitle}>Change Password</h3>
                <div className={styles.formGroup}>
                  <label className={styles.label}>Current Password</label>
                  <input type="password" className={styles.input} placeholder="••••••••" />
                </div>
                <div className={styles.formGroup}>
                  <label className={styles.label}>New Password</label>
                  <input type="password" className={styles.input} placeholder="••••••••" />
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <button className={`${styles.btn} ${styles.btnPrimary}`}>Update Password</button>
                </div>
              </div>

              <div className={styles.card}>
                <h3 className={styles.cardTitle}>Infrastructure Access</h3>
                <p style={{ fontSize: 14, color: 'var(--t3)', margin: 0 }}>
                  API Keys and LLM Provider credentials are now managed in the **AI Infrastructure** module.
                </p>
                <div>
                  <button className={styles.btn} onClick={() => window.location.href='/models'}>
                    Go to Infrastructure Settings →
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* SECTION: BILLING */}
          {activeTab === 'billing' && (
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.title}>Billing & Plans</h2>
                <p className={styles.subtitle}>Manage your subscription and usage invoices.</p>
              </div>

              <div className={styles.card} style={{ textAlign: 'center', padding: '60px 20px' }}>
                <div style={{ fontSize: 40, marginBottom: 20 }}>💳</div>
                <h3 className={styles.cardTitle}>Enterprise Tier</h3>
                <p style={{ color: 'var(--t3)', maxWidth: 400, margin: '0 auto 24px' }}>
                  Your workspace is currently on the Enterprise custom plan. 
                  Invoices are generated on the 1st of every month.
                </p>
                <button 
                  className={`${styles.btn} ${styles.btnPrimary}`}
                  onClick={() => window.location.href='/billing'}
                >
                  View Billing Dashboard
                </button>
              </div>
            </div>
          )}

        </div>
      </div>
    </DashboardLayout>
  );
}
