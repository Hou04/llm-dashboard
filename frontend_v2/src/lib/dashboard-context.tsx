/**
 * Dashboard Context — shared state for tenant selection and period.
 *
 * Two-Personality Behavior:
 *   - Super Admin: Can freely change selectedTenant (including "all")
 *   - Tenant Admin/Viewer: Locked to their own tenant_id from Auth.
 *     The setter is a no-op — they cannot change their tenant scope.
 *
 * This ensures zero data-leakage at the frontend level:
 * even if React state could be manipulated, the backend
 * enforces the same scoping via get_tenant_scope.
 */

'use client';

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react';
import { useAuth } from '@/lib/auth';

interface DashboardContextValue {
  selectedTenant: string;
  setSelectedTenant: (tenant: string) => void;
  period: number;
  setPeriod: (days: number) => void;
  /** Convenience: returns tenant_id or undefined for "all" */
  tenantParam: string | undefined;
  /** True if the tenant is locked (non-super-admin user) */
  isTenantLocked: boolean;
}

const DashboardContext = createContext<DashboardContextValue | undefined>(undefined);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const { user, isAuthenticated } = useAuth();
  const [selectedTenant, setSelectedTenantState] = useState('');
  const [period, setPeriod] = useState(30);

  const isSuperAdmin = user?.role === 'super_admin';
  const isTenantLocked = !isSuperAdmin && !!user?.tenant_id;

  // Auto-lock tenant for non-super-admin users
  useEffect(() => {
    if (isAuthenticated && !isSuperAdmin && user?.tenant_id) {
      setSelectedTenantState(user.tenant_id);
    }
  }, [isAuthenticated, isSuperAdmin, user?.tenant_id]);

  // Guarded setter: tenant users cannot change their scope
  const handleSetTenant = useCallback(
    (t: string) => {
      if (isTenantLocked) return; // No-op for tenant users
      setSelectedTenantState(t);
    },
    [isTenantLocked]
  );

  const handleSetPeriod = useCallback((d: number) => setPeriod(d), []);

  return (
    <DashboardContext.Provider
      value={{
        selectedTenant,
        setSelectedTenant: handleSetTenant,
        period,
        setPeriod: handleSetPeriod,
        tenantParam: selectedTenant || undefined,
        isTenantLocked,
      }}
    >
      {children}
    </DashboardContext.Provider>
  );
}

export function useDashboard(): DashboardContextValue {
  const ctx = useContext(DashboardContext);
  if (!ctx) throw new Error('useDashboard must be within DashboardProvider');
  return ctx;
}
