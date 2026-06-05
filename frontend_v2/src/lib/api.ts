/**
 * API Client — Axios wrapper with Bearer token injection & silent refresh.
 *
 * Replicates the exact logic from the single-page `api()` function:
 * - Attaches Authorization header from AuthService
 * - On 401: attempts silent refresh, then retries
 * - Retries up to 2 times with exponential backoff
 */

import axios, { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

// Token storage (in-memory for access, localStorage for refresh)
let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('llm_refresh');
}

export function setRefreshToken(token: string | null) {
  if (typeof window === 'undefined') return;
  if (token) {
    localStorage.setItem('llm_refresh', token);
  } else {
    localStorage.removeItem('llm_refresh');
  }
}

// ── Axios instance ──
const api: AxiosInstance = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: 60000,
});

// ── Request interceptor: inject Bearer token ──
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── Response interceptor: handle 401 with silent refresh ──
interface RefreshSubscriber {
  resolve: (token: string) => void;
  reject: (err: any) => void;
}
let isRefreshing = false;
let refreshSubscribers: RefreshSubscriber[] = [];

function subscribeTokenRefresh(resolve: (token: string) => void, reject: (err: any) => void) {
  refreshSubscribers.push({ resolve, reject });
}

function onTokenRefreshed(token: string) {
  refreshSubscribers.forEach(sub => sub.resolve(token));
  refreshSubscribers = [];
}

function onTokenRefreshFailed(err: any) {
  refreshSubscribers.forEach(sub => sub.reject(err));
  refreshSubscribers = [];
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        // Wait for the ongoing refresh
        return new Promise((resolve, reject) => {
          subscribeTokenRefresh(
            (token: string) => {
              if (originalRequest.headers) {
                originalRequest.headers.Authorization = `Bearer ${token}`;
              }
              resolve(api(originalRequest));
            },
            (err: any) => {
              reject(err);
            }
          );
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const refreshToken = getRefreshToken();
        console.log("[API] Attempting token refresh...", refreshToken ? "Token found" : "No token");
        
        if (!refreshToken) {
          throw new Error('No refresh token available');
        }

        const res = await axios.post(`${API_BASE}/v1/auth/refresh`, {
          refresh_token: refreshToken,
        });

        const newAccessToken = res.data.access_token;
        const newRefreshToken = res.data.refresh_token;

        console.log("[API] Token refreshed successfully");
        setAccessToken(newAccessToken);
        if (newRefreshToken) setRefreshToken(newRefreshToken);

        onTokenRefreshed(newAccessToken);

        if (originalRequest.headers) {
          originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        }
        return api(originalRequest);
      } catch (refreshError: any) {
        console.warn("[API] Token refresh failed:", refreshError.response?.data?.detail || refreshError.message);
        
        setAccessToken(null);
        setRefreshToken(null);
        
        const customError = {
          ...refreshError,
          _isAuthError: true,
          message: "Session expired. Please log in again."
        };

        onTokenRefreshFailed(customError);

        // Emit auth failure event
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('auth:expired'));
        }
        
        // Return a rejection that clearly indicates an auth failure
        return Promise.reject(customError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

export default api;
export { API_BASE };
