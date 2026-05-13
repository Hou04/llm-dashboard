/**
 * Topbar — tenant selector, period buttons, theme toggle, user chip.
 *
 * Two-Personality Behavior:
 *   - Super Admin: Full tenant selector dropdown, period controls, health indicators.
 *   - Tenant Admin/Viewer: Tenant selector is HIDDEN. Shows a locked scope
 *     indicator instead. Period controls remain available.
 */

'use client';

import { useEffect, useState, useCallback } from 'react';
import styles from './Topbar.module.css';
import { useAuth } from '@/lib/auth';
import api from '@/lib/api';
import { useI18n, LOCALES, Locale } from '@/lib/i18n';

interface TopbarProps {
  title: string;
  meta?: string;
  selectedTenant: string;
  onTenantChange: (tenant: string) => void;
  period: number;
  onPeriodChange: (days: number) => void;
  isTenantLocked?: boolean;
}

export default function Topbar({
  title,
  meta,
  selectedTenant,
  onTenantChange,
  period,
  onPeriodChange,
  isTenantLocked = false,
}: TopbarProps) {
  const { user, logout } = useAuth();
  const { locale, setLocale } = useI18n();
  const [tenants, setTenants] = useState<string[]>([]);
  const [theme, setTheme] = useState<'light' | 'dark'>('dark');

  // Fetch tenants (only needed for super_admin)
  const isSuperAdmin = user?.role === 'super_admin';

  useEffect(() => {
    if (!isSuperAdmin) return; // Tenant users don't need the tenant list
    const fetchTenants = async () => {
      try {
        const res = await api.get('/v1/dashboard/tenants');
        const list = (res.data.tenants || res.data || []).map(
          (t: { tenant_id?: string } | string) =>
            typeof t === 'string' ? t : t.tenant_id || ''
        );
        setTenants(list.filter(Boolean));
      } catch {
        setTenants([]);
      }
    };
    fetchTenants();
  }, [isSuperAdmin]);

  // Init theme (client-side only to avoid hydration mismatch)
  useEffect(() => {
    const savedTheme = localStorage.getItem('theme') as 'light' | 'dark' | null;
    const initial = savedTheme || 'light';
    setTheme(initial);
    document.documentElement.className = initial;
  }, []);

  // Theme toggle
  const toggleTheme = useCallback(() => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    localStorage.setItem('theme', next);
    document.documentElement.className = next;
  }, [theme]);

  // Lock tenant for non-super-admins
  useEffect(() => {
    if (!isSuperAdmin && user?.tenant_id) {
      onTenantChange(user.tenant_id);
    }
  }, [isSuperAdmin, user?.tenant_id, onTenantChange]);

  const initials = (user?.username || '?').substring(0, 2).toUpperCase();
  const roleName = (user?.role || '').replace(/_/g, ' ');
  const roleClass = user?.role === 'super_admin'
    ? styles.roleSuper
    : user?.role === 'tenant_admin'
      ? styles.roleAdmin
      : styles.roleViewer;

  return (
    <div className={styles.topbar}>
      <div className={styles.left}>
        <div className={styles.pageTitle}>{title}</div>
        {meta && <div className={styles.pageMeta}>{meta}</div>}
      </div>

      <div className={styles.right}>
        {/* Tenant selector — ONLY for super_admin */}
        {isSuperAdmin && (
          <select
            className={styles.tenantSelect}
            value={selectedTenant}
            onChange={(e) => onTenantChange(e.target.value)}
          >
            <option value="">All tenants</option>
            {tenants.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        )}

        {/* Locked scope indicator — for tenant users */}
        {!isSuperAdmin && user?.tenant_id && (
          <div className={styles.scopeIndicator}>
            <span className={styles.scopeLock}>🔒</span>
            <span className={styles.scopeLabel}>{user.tenant_id}</span>
          </div>
        )}

        {/* Period buttons */}
        {[7, 14, 30].map((d) => (
          <button
            key={d}
            className={`${styles.periodBtn} ${period === d ? styles.active : ''}`}
            onClick={() => onPeriodChange(d)}
          >
            {d}d
          </button>
        ))}

        {/* Language selector */}
        <select
          className={styles.tenantSelect}
          value={locale}
          onChange={(e) => setLocale(e.target.value as Locale)}
          title="Change language"
        >
          {LOCALES.map((l) => (
            <option key={l.code} value={l.code}>
              {l.flag} {l.code.toUpperCase()}
            </option>
          ))}
        </select>

        {/* Theme toggle */}
        <button className={styles.themeToggle} onClick={toggleTheme}>
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>

        {/* User chip */}
        {user && (
          <div className={styles.userChip}>
            <div className={styles.avatar}>{initials}</div>
            <div>
              <div className={styles.userName}>{user.username}</div>
              <span className={`${styles.roleBadge} ${roleClass}`}>
                {roleName}
              </span>
            </div>
          </div>
        )}

        {/* Logout */}
        <button className={styles.logoutBtn} onClick={logout}>
          Logout
        </button>
      </div>
    </div>
  );
}
