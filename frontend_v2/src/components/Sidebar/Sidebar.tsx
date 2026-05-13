/**
 * Sidebar — Notion-style clean navigation with SVG icons.
 *
 * Hierarchy:
 *   Super Admin → Full "Infrastructure Cockpit" (Global control)
 *   Tenant Admin → "Organization Manager" (Prompts, Users, Experiments)
 *   Member → "Developer View" (Costs, Playground, Dashboard)
 */

'use client';

import { ReactNode, useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import styles from './Sidebar.module.css';
import api from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useI18n, Translations, LOCALES, Locale } from '@/lib/i18n';
import {
  IconDashboard, IconHome, IconGateway, IconCost, IconSearch,
  IconBulb, IconGear, IconChart, IconShield, IconPen,
  IconClipboard, IconFlask, IconUsers, IconBell, IconFile,
  IconBox, IconPlay, IconSparkle, IconBuilding,
} from '@/components/Icons';

type SidebarKey = keyof Translations['sidebar'];

interface NavItem {
  id: string;
  labelKey: SidebarKey;
  icon: ReactNode;
  href: string;
  badge?: boolean;
  adminOnly?: boolean;  // Visible to Tenant Admin & Super Admin
  superOnly?: boolean;  // Visible ONLY to Super Admin
}

interface NavSection {
  titleKey?: SidebarKey;
  items: NavItem[];
  adminOnly?: boolean;
  superOnly?: boolean;
}

const NAV_ITEMS: NavSection[] = [
  {
    items: [
      { id: 'exec',   labelKey: 'executiveOverview', icon: <IconDashboard size={16} />, href: '/',              superOnly: true },
      { id: 'mydash', labelKey: 'myDashboard',       icon: <IconHome size={16} />,      href: '/my-dashboard' },
    ],
  },
  {
    titleKey: 'analytics',
    superOnly: true, // Global analytics are super admin only
    items: [
      { id: 'm1',     labelKey: 'apiGateway',        icon: <IconGateway size={16} />,   href: '/gateway' },
      { id: 'm2',     labelKey: 'costAnalytics',     icon: <IconCost size={16} />,      href: '/costs' },
      { id: 'm3',     labelKey: 'anomalyDetection',  icon: <IconSearch size={16} />,    href: '/anomalies',     badge: true },
      { id: 'm4',     labelKey: 'aiExplainer',       icon: <IconBulb size={16} />,      href: '/explainer' },
    ],
  },
  {
    titleKey: 'intelligence',
    superOnly: true,
    items: [
      { id: 'm5', labelKey: 'modelOptimizer',  icon: <IconGear size={16} />,   href: '/optimizer' },
      { id: 'm6', labelKey: 'forecasting',      icon: <IconChart size={16} />,  href: '/forecasting' },
      { id: 'm7', labelKey: 'governanceRules', icon: <IconShield size={16} />, href: '/governance' },
    ],
  },
  {
    titleKey: 'tools',
    items: [
      { id: 'prompts',     labelKey: 'promptCms',      icon: <IconPen size={16} />,       href: '/prompts',     adminOnly: true },
      { id: 'sessions',    labelKey: 'sessionTracing',  icon: <IconClipboard size={16} />, href: '/sessions',    adminOnly: true },
      { id: 'experiments', labelKey: 'abExperiments',  icon: <IconFlask size={16} />,     href: '/experiments', adminOnly: true },
      { id: 'audit',       labelKey: 'complianceAudit', icon: <IconSearch size={16} />,    href: '/audit',       adminOnly: true },
      { id: 'tenants',     labelKey: 'tenantManagement', icon: <IconBuilding size={16} />, href: '/tenants',     superOnly: true },
      { id: 'users',       labelKey: 'userManagement',  icon: <IconUsers size={16} />,     href: '/users',       superOnly: true },
      { id: 'alerts',      labelKey: 'alertSettings',   icon: <IconBell size={16} />,      href: '/alerts' },
    ],
  },
  {
    titleKey: 'workspace',
    items: [
      { id: 'm10',       labelKey: 'billingInvoices', icon: <IconFile size={16} />,    href: '/billing' },
      { id: 'catalog',   labelKey: 'modelCatalog',      icon: <IconBox size={16} />,     href: '/models' },
      { id: 'playground', labelKey: 'playground',         icon: <IconPlay size={16} />,    href: '/playground' },
      { id: 'settings',  labelKey: 'settings',           icon: <IconGear size={16} />,    href: '/settings' },
    ],
  },
  {
    items: [
      { id: 'm8', labelKey: 'aiAssistant', icon: <IconSparkle size={16} />, href: '/assistant' },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();
  const { t, locale, setLocale } = useI18n();
  const [anomalyCount, setAnomalyCount] = useState(0);

  const isSuperAdmin = user?.role === 'super_admin';
  const isTenantAdmin = user?.role === 'tenant_admin' || isSuperAdmin;

  const filteredNav = NAV_ITEMS
    .filter((section) => {
      if (section.superOnly && !isSuperAdmin) return false;
      if (section.adminOnly && !isTenantAdmin) return false;
      return true;
    })
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        if (item.superOnly && !isSuperAdmin) return false;
        if (item.adminOnly && !isTenantAdmin) return false;
        return true;
      }),
    }))
    .filter((section) => section.items.length > 0);

  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        const res = await api.get('/v1/dashboard/alerts?anomaly_hours=24');
        setAnomalyCount(res.data.critical_count + res.data.warning_count);
      } catch { /* ignore */ }
    };
    fetchAlerts();
    const interval = setInterval(fetchAlerts, 60_000);
    return () => clearInterval(interval);
  }, []);

  const personaLabel = isSuperAdmin ? t.sidebar.infrastructure : t.sidebar.memberCenter;

  return (
    <aside className={styles.sidebar}>
      {/* Logo mark */}
      <div className={styles.logo}>
        <div className={styles.logoMark}>L</div>
        <div>
          <div className={styles.logoTitle}>LLM Control Plane</div>
          <div className={styles.logoSub}>{personaLabel}</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className={styles.nav}>
        {filteredNav.map((section, si) => (
          <div key={si}>
            {si > 0 && <div className={styles.navDivider} />}
            {section.titleKey && (
              <div className={styles.sectionLabel}>{t.sidebar[section.titleKey]}</div>
            )}
            {section.items.map((item) => {
              const isActive = pathname === item.href ||
                (item.href !== '/' && item.href !== '/my-dashboard' && pathname.startsWith(item.href));
              const label = t.sidebar[item.labelKey];
              return (
                <button
                  key={item.id}
                  className={`${styles.navBtn} ${isActive ? styles.active : ''}`}
                  onClick={() => router.push(item.href)}
                  data-tooltip={label}
                >
                  <span className={styles.navIcon}>{item.icon}</span>
                  <span className={styles.navLabel}>{label}</span>
                  {item.badge && anomalyCount > 0 && (
                    <span className={styles.navBadge}>{anomalyCount}</span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className={styles.sidebarFooter}>
        {isSuperAdmin && (
          <div className={styles.healthRow}>
            <span className={`${styles.dot} ${styles.dotOk}`} />
            <span className={styles.healthLabel}>{t.sidebar.allSystemsOk}</span>
          </div>
        )}

        {!isSuperAdmin && user?.tenant_id && (
          <div className={styles.tenantBadge}>
            <span className={styles.tenantBadgeIcon}><IconBuilding size={14} /></span>
            <span className={styles.tenantBadgeLabel}>{user.tenant_id}</span>
          </div>
        )}

        <select
          className={styles.localeSwitcher}
          value={locale}
          onChange={(e) => setLocale(e.target.value as Locale)}
        >
          {LOCALES.map((l) => (
            <option key={l.code} value={l.code}>
              {l.flag} {l.label}
            </option>
          ))}
        </select>
      </div>
    </aside>
  );
}
