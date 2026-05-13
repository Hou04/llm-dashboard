/**
 * LoginOverlay — Full-screen auth modal.
 *
 * Replicates the Auth.showOverlay() / Auth.login() from single page.
 * Shown when user is not authenticated.
 */

'use client';

import { useState, KeyboardEvent } from 'react';
import styles from './LoginOverlay.module.css';
import { useAuth } from '@/lib/auth';

export default function LoginOverlay() {
  const { login, signup, isLoading, error } = useAuth();
  const [isSignup, setIsSignup] = useState(false);
  
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [tenantName, setTenantName] = useState('');
  
  const [localError, setLocalError] = useState('');

  const handleSubmit = async () => {
    if (!username.trim() || !password) {
      setLocalError('Please enter username and password.');
      return;
    }
    if (isSignup && !tenantName.trim()) {
      setLocalError('Please enter your organization name.');
      return;
    }
    
    setLocalError('');
    try {
      if (isSignup) {
        await signup(username.trim(), password, tenantName.trim());
      } else {
        await login(username.trim(), password);
      }
    } catch (e: unknown) {
      setLocalError(e instanceof Error ? e.message : 'Authentication failed');
    }
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter') handleSubmit();
  };

  const fillDemo = (user: string, pass: string) => {
    setIsSignup(false);
    setUsername(user);
    setPassword(pass);
  };

  const displayError = localError || error;

  return (
    <div className={styles.overlay}>
      <div className={styles.card}>
        <div className={styles.logoWrap}>
          <div className={styles.logoIcon}>L</div>
          <div className={styles.logoName}>LLM Control Plane</div>
          <div className={styles.logoSub}>Enterprise Governance & Infrastructure</div>
        </div>

        {displayError && (
          <div className={styles.error}>
            ⚠ {typeof displayError === 'string' 
              ? displayError 
              : (Array.isArray(displayError) && (displayError as any[])[0]?.msg)
                ? (displayError as any[])[0].msg
                : JSON.stringify(displayError)}
          </div>
        )}

        {isSignup && (
          <div className={styles.field}>
            <label className={styles.label}>Organization Name</label>
            <input
              className={styles.input}
              type="text"
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="E.g. Acme Corp"
              autoFocus
            />
          </div>
        )}

        <div className={styles.field}>
          <label className={styles.label}>Username</label>
          <input
            className={styles.input}
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter username"
            autoFocus={!isSignup}
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Password</label>
          <input
            className={styles.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter password"
          />
          {isSignup && (
            <div style={{fontSize: '11px', color: '#666', marginTop: '4px'}}>
              Must contain at least 8 chars, 1 uppercase, 1 digit.
            </div>
          )}
        </div>

        <button
          className={styles.submitBtn}
          onClick={handleSubmit}
          disabled={isLoading}
        >
          {isLoading ? 'Processing…' : (isSignup ? 'Create Account' : 'Sign in')}
        </button>

        <div style={{ textAlign: 'center', marginTop: '1rem', fontSize: '14px' }}>
          <button 
            className={styles.demoBtn} 
            onClick={() => {
              setIsSignup(!isSignup);
              setLocalError('');
            }}
          >
            {isSignup ? 'Already have an account? Sign in' : 'Need an account? Sign up'}
          </button>
        </div>

        {!isSignup && (
          <div className={styles.demoHint}>
            Demo account:{' '}
            <button className={styles.demoBtn} onClick={() => fillDemo('admin', 'Admin@1234')}>
              Super Admin
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
