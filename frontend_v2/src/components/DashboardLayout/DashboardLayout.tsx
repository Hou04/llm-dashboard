/**
 * DashboardLayout — the authenticated shell.
 *
 * Renders: Sidebar | Main(Topbar + Content)
 * Shows LoginOverlay when not authenticated.
 */

'use client';

import { ReactNode } from 'react';
import styles from './DashboardLayout.module.css';
import Sidebar from '@/components/Sidebar/Sidebar';
import Topbar from '@/components/Topbar/Topbar';
import LoginOverlay from '@/components/LoginOverlay/LoginOverlay';
import { useAuth } from '@/lib/auth';
import { useDashboard } from '@/lib/dashboard-context';

interface DashboardLayoutProps {
  children: ReactNode;
  title: string;
  meta?: string;
}

export default function DashboardLayout({ children, title, meta }: DashboardLayoutProps) {
  const { isAuthenticated, isLoading } = useAuth();
  const { selectedTenant, setSelectedTenant, period, setPeriod, isTenantLocked } = useDashboard();

  // Loading state
  if (isLoading) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        background: '#ffffff',
        color: '#787774',
        gap: 10,
        fontSize: 14,
      }}>
        <div className="spinner" />
        Initializing…
      </div>
    );
  }

  // Not authenticated → show login
  if (!isAuthenticated) {
    return <LoginOverlay />;
  }

  return (
    <div className={styles.layout}>
      <Sidebar />
      <div className={styles.main}>
        <Topbar
          title={title}
          meta={meta}
          selectedTenant={selectedTenant}
          onTenantChange={setSelectedTenant}
          period={period}
          onPeriodChange={setPeriod}
          isTenantLocked={isTenantLocked}
        />
        <div className={styles.scroll}>
          {children}
        </div>
      </div>
    </div>
  );
}
