/**
 * Auth Context & Hook — manages the full authentication lifecycle.
 *
 * Replicates Auth object from single page:
 * - Login with username/password
 * - Silent refresh on app load
 * - Role-based access (super_admin, tenant_admin, tenant_viewer)
 * - Tenant scoping
 */

'use client';

import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import api, { setAccessToken, setRefreshToken, getRefreshToken, API_BASE } from '@/lib/api';
import axios from 'axios';

// ── Types ──

export interface User {
  id: string;
  username: string;
  email: string;
  role: 'super_admin' | 'tenant_admin' | 'tenant_viewer';
  tenant_id: string | null;
  is_active: boolean;
}

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
}

interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<void>;
  signup: (username: string, password: string, tenantName: string, email?: string) => Promise<void>;
  logout: () => Promise<void>;
  isSuperAdmin: () => boolean;
  isAdmin: () => boolean;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

// ── Provider ──

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    isAuthenticated: false,
    isLoading: true,
    error: null,
  });

  const fetchUser = useCallback(async () => {
    try {
      const res = await api.get('/v1/auth/me');
      setState(prev => ({
        ...prev,
        user: res.data,
        isAuthenticated: true,
        isLoading: false,
        error: null,
      }));
    } catch {
      setState(prev => ({
        ...prev,
        user: null,
        isAuthenticated: false,
        isLoading: false,
      }));
    }
  }, []);

  // ── Initialization ──
  useEffect(() => {
    const token = getRefreshToken();
    if (token) {
      fetchUser();
    } else {
      setState(prev => ({ ...prev, isLoading: false }));
    }
  }, [fetchUser]);

  // ── Listen for auth expiry events ──
  useEffect(() => {
    const handleExpired = () => {
      setState({
        user: null,
        isAuthenticated: false,
        isLoading: false,
        error: 'Session expired — please sign in again',
      });
    };

    window.addEventListener('auth:expired', handleExpired);
    return () => window.removeEventListener('auth:expired', handleExpired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setState(prev => ({ ...prev, isLoading: true, error: null }));

    try {
      const res = await axios.post(`${API_BASE}/v1/auth/login`, {
        username,
        password,
      });

      setAccessToken(res.data.access_token);
      setRefreshToken(res.data.refresh_token);
      await fetchUser();
    } catch (err: unknown) {
      const message = axios.isAxiosError(err)
        ? err.response?.data?.detail || 'Invalid credentials'
        : 'Login failed';
      setState(prev => ({
        ...prev,
        isLoading: false,
        error: message,
      }));
      throw new Error(message);
    }
  }, [fetchUser]);

  const signup = useCallback(async (username: string, password: string, tenantName: string, email?: string) => {
    setState(prev => ({ ...prev, isLoading: true, error: null }));

    try {
      const res = await axios.post(`${API_BASE}/v1/auth/signup`, {
        username,
        password,
        tenant_name: tenantName,
        email,
      });

      setAccessToken(res.data.access_token);
      setRefreshToken(res.data.refresh_token);
      await fetchUser();
    } catch (err: unknown) {
      const message = axios.isAxiosError(err)
        ? err.response?.data?.detail || 'Signup failed. Please try again.'
        : 'Signup failed';
      setState(prev => ({
        ...prev,
        isLoading: false,
        error: message,
      }));
      throw new Error(message);
    }
  }, [fetchUser]);

  const logout = useCallback(async () => {
    try {
      await api.post('/v1/auth/logout');
    } catch {
      // Ignore logout errors
    }
    setAccessToken(null);
    setRefreshToken(null);
    setState({
      user: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,
    });
  }, []);

  const isSuperAdmin = useCallback(() => state.user?.role === 'super_admin', [state.user]);
  const isAdmin = useCallback(
    () => state.user?.role === 'super_admin' || state.user?.role === 'tenant_admin',
    [state.user]
  );

  return (
    <AuthContext.Provider value={{ ...state, login, signup, logout, isSuperAdmin, isAdmin }}>
      {children}
    </AuthContext.Provider>
  );
}

// ── Hook ──

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
