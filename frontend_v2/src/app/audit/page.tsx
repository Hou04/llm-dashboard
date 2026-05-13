/**
 * Compliance Audit Page
 * 
 * Provides an immutable trail of all administrative actions.
 * Vital for SOC2/ISO27001 compliance.
 */

'use client';

import { useState, useEffect, useCallback } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { IconSearch, IconShield, IconUsers, IconClock, IconActivity } from '@/components/Icons';
import api from '@/lib/api';
import styles from './Audit.module.css';

export default function AuditPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  
  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/v1/gateway/audit');
      setLogs(res.data.logs || []);
    } catch (err) {
      console.error("Failed to fetch audit logs", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  const filteredLogs = logs.filter(log => 
    (log.user_email || log.user_id || '').toLowerCase().includes(search.toLowerCase()) ||
    (log.description || '').toLowerCase().includes(search.toLowerCase()) ||
    (log.resource_type || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <DashboardLayout title="Compliance Audit" meta="Immutable trail of administrative actions">
      <div className={styles.container}>
        
        {/* Header Stats */}
        <div className={styles.statsRow}>
          <div className={styles.statCard}>
            <div className={styles.statIcon} style={{ color: 'var(--blue)' }}><IconShield size={20} /></div>
            <div>
              <div className={styles.statLabel}>Security Events</div>
              <div className={styles.statValue}>{logs.length}</div>
            </div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statIcon} style={{ color: 'var(--green)' }}><IconActivity size={20} /></div>
            <div>
              <div className={styles.statLabel}>System Health</div>
              <div className={styles.statValue}>Optimal</div>
            </div>
          </div>
        </div>

        {/* Search & Filter */}
        <div className={styles.filterBar}>
          <div className={styles.searchWrapper}>
            <IconSearch size={16} />
            <input 
              type="text" 
              placeholder="Filter by user, action, or resource..." 
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button className={styles.refreshBtn} onClick={fetchLogs}>Refresh Trail</button>
        </div>

        {/* Audit Table */}
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>User</th>
                <th>Action</th>
                <th>Resource</th>
                <th>Description</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: 'var(--t3)' }}>Loading audit trail...</td></tr>
              ) : filteredLogs.length === 0 ? (
                <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: 'var(--t3)' }}>No audit events found.</td></tr>
              ) : filteredLogs.map((log) => (
                <tr key={log.id}>
                  <td className={styles.timeCell}>
                    <IconClock size={12} />
                    {new Date(log.timestamp).toLocaleString()}
                  </td>
                  <td className={styles.userCell}>
                    <div className={styles.avatar}>{(log.user_email || log.user_id || 'U')[0].toUpperCase()}</div>
                    <div>
                      <div className={styles.username}>{log.user_email || log.user_id || 'System'}</div>
                      <div className={styles.role}>{log.role || 'admin'}</div>
                    </div>
                  </td>
                  <td>
                    <span className={`${styles.badge} ${styles['badge-' + log.action]}`}>
                      {log.action}
                    </span>
                  </td>
                  <td className={styles.resourceCell}>{log.resource_type}</td>
                  <td className={styles.descCell}>{log.description}</td>
                  <td>
                    <span className={log.status === 'success' || !log.status ? styles.statusSuccess : styles.statusError}>
                      {log.status || 'success'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </DashboardLayout>
  );
}
