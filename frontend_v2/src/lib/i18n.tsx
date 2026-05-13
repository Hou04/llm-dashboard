'use client';

import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';

// ── Import translations ──
import { en } from './translations/en';
import { fr } from './translations/fr';
import { frBE } from './translations/fr-BE';
import { es } from './translations/es';
import { de } from './translations/de';

// ── Supported locales ──
export type Locale = 'en' | 'fr' | 'fr-BE' | 'es' | 'de';

export const LOCALES: { code: Locale; label: string; flag: string }[] = [
  { code: 'en',    label: 'English',         flag: '🇬🇧' },
  { code: 'fr',    label: 'Français',        flag: '🇫🇷' },
  { code: 'fr-BE', label: 'Français (Belge)', flag: '🇧🇪' },
  { code: 'es',    label: 'Español',         flag: '🇪🇸' },
  { code: 'de',    label: 'Deutsch',         flag: '🇩🇪' },
];

// ── Translation dictionary type ──
export interface Translations {
  // Sidebar
  sidebar: {
    infrastructure: string;
    memberCenter: string;
    executiveOverview: string;
    myDashboard: string;
    analytics: string;
    apiGateway: string;
    costAnalytics: string;
    anomalyDetection: string;
    aiExplainer: string;
    intelligence: string;
    modelOptimizer: string;
    forecasting: string;
    governanceRules: string;
    tools: string;
    promptCms: string;
    sessionTracing: string;
    abExperiments: string;
    complianceAudit: string;
    userManagement: string;
    tenantManagement: string;
    alertSettings: string;
    workspace: string;
    billingInvoices: string;
    modelCatalog: string;
    playground: string;
    settings: string;
    aiAssistant: string;
    allSystemsOk: string;
  };
  // Users page
  users: {
    title: string;
    subtitle: string;
    refresh: string;
    newUser: string;
    totalUsers: string;
    active: string;
    admins: string;
    tenants: string;
    acrossAllTenants: string;
    currentlyEnabled: string;
    superTenantAdmins: string;
    activeOrganizations: string;
    searchPlaceholder: string;
    allRoles: string;
    allStatus: string;
    inactive: string;
    user: string;
    role: string;
    tenant: string;
    status: string;
    lastLogin: string;
    created: string;
    actions: string;
    noEmail: string;
    system: string;
    never: string;
    showing: string;
    of: string;
    loading: string;
    noUsersFound: string;
    accessDenied: string;
    accessDeniedMsg: string;
    // Roles
    superAdmin: string;
    tenantAdmin: string;
    tenantViewer: string;
    // Modals
    createUser: string;
    editUser: string;
    userDetails: string;
    resetPassword: string;
    confirmDeactivation: string;
    confirmReactivation: string;
    username: string;
    email: string;
    password: string;
    newPassword: string;
    cancel: string;
    save: string;
    creating: string;
    saving: string;
    resetting: string;
    deactivating: string;
    reactivating: string;
    deactivateUser: string;
    reactivateUser: string;
    deactivateMsg: string;
    reactivateMsg: string;
    passwordHint: string;
    passwordLoginHint: string;
    tenantHint: string;
    // Toast
    userCreated: string;
    userUpdated: string;
    passwordReset: string;
    userDeactivated: string;
    userReactivated: string;
    // Detail
    userId: string;
    lastActivity: string;
    edit: string;
    viewDetails: string;
  };
  // Common
  common: {
    search: string;
    filter: string;
    export: string;
    close: string;
    confirm: string;
    delete: string;
    back: string;
    next: string;
    previous: string;
    none: string;
  };
  // Login
  login: {
    title: string;
    subtitle: string;
    signIn: string;
    signUp: string;
    usernameLabel: string;
    passwordLabel: string;
    forgotPassword: string;
    noAccount: string;
    hasAccount: string;
    tenantName: string;
  };
}

// ── Import translations ──

const translations: Record<Locale, Translations> = { en, fr, 'fr-BE': frBE, es, de };

// ── Context ──
interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: Translations;
}

const I18nContext = createContext<I18nContextValue | undefined>(undefined);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (typeof window === 'undefined') return 'en';
    return (localStorage.getItem('llm_locale') as Locale) || 'en';
  });

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    if (typeof window !== 'undefined') localStorage.setItem('llm_locale', l);
  }, []);

  const t = translations[locale];

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within I18nProvider');
  return ctx;
}
