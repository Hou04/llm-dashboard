'use client';

import { useState, useEffect, useCallback } from 'react';
import s from './page.module.css';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import api from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useI18n } from '@/lib/i18n';
import {
  IconEye, IconEdit, IconKey, IconBan, IconCheck, IconRefresh,
  IconPlus, IconX, IconSearch, IconWarn, IconArrowLoop, IconUsers,
} from '@/components/Icons';

interface User {
  id: string; username: string; email: string | null; role: string;
  tenant_id: string | null; is_active: boolean; created_at: string;
  last_login_at: string | null; last_activity_at: string | null;
}

interface PaginatedResponse {
  users: User[]; total: number; page: number; page_size: number; total_pages: number;
}

type ModalType = 'create' | 'edit' | 'view' | 'resetPw' | 'confirmDelete' | 'confirmReactivate' | null;
interface Toast { message: any; type: 'success' | 'error'; }

const ROLES = ['super_admin', 'tenant_admin', 'tenant_viewer'] as const;
const roleClass = (r: string) => r === 'super_admin' ? s.roleSuperAdmin : r === 'tenant_admin' ? s.roleTenantAdmin : s.roleTenantViewer;
const avatarClass = (r: string) => r === 'super_admin' ? s.avatarSuperAdmin : r === 'tenant_admin' ? s.avatarTenantAdmin : s.avatarTenantViewer;
const initials = (name: string) => name.slice(0, 2).toUpperCase();

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const { t, locale } = useI18n();
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [modal, setModal] = useState<ModalType>(null);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [formData, setFormData] = useState({ username: '', password: '', email: '', role: 'tenant_viewer', tenant_id: '', new_password: '' });
  const [submitting, setSubmitting] = useState(false);
  const [tenants, setTenants] = useState<string[]>([]);

  // Helpers with translation context
  const getRoleLabel = (r: string) => {
    if (r === 'super_admin') return t.users.superAdmin;
    if (r === 'tenant_admin') return t.users.tenantAdmin;
    if (r === 'tenant_viewer') return t.users.tenantViewer;
    return r;
  };
  const fmtDate = (d: string | null) => d ? new Date(d).toLocaleDateString(locale, { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
  const fmtDateTime = (d: string | null) => d ? new Date(d).toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : t.users.never;

  const showToast = (message: any, type: 'success' | 'error' = 'success') => {
    let msg = 'Error';
    if (typeof message === 'string') {
      msg = message;
    } else if (message && typeof message === 'object') {
      // If it's a Pydantic error list, take the first one
      if (Array.isArray(message) && message[0]?.msg) {
        msg = message[0].msg;
      } else {
        msg = JSON.stringify(message);
      }
    }
    setToast({ message: msg, type });
    setTimeout(() => setToast(null), 3500);
  };

  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ page: String(page), page_size: '15' });
      if (search) params.set('search', search);
      if (roleFilter !== 'all') params.set('role', roleFilter);
      if (statusFilter !== 'all') params.set('status_filter', statusFilter);
      const res = await api.get(`/v1/auth/users?${params}`);
      setData(res.data);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to fetch users');
    } finally {
      setLoading(false);
    }
  }, [page, search, roleFilter, statusFilter]);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  useEffect(() => {
    api.get('/v1/tenants').then(r => setTenants(r.data.map((t: any) => t.tenant_id))).catch(() => {});
  }, []);

  // Debounced search
  const [searchInput, setSearchInput] = useState('');
  useEffect(() => {
    const timeOutId = setTimeout(() => { setSearch(searchInput); setPage(1); }, 350);
    return () => clearTimeout(timeOutId);
  }, [searchInput]);

  const openCreate = () => {
    setFormData({ username: '', password: '', email: '', role: 'tenant_viewer', tenant_id: '', new_password: '' });
    setModal('create');
  };

  const openEdit = (u: User) => {
    setSelectedUser(u);
    setFormData({ username: u.username, password: '', email: u.email || '', role: u.role, tenant_id: u.tenant_id || '', new_password: '' });
    setModal('edit');
  };

  const openView = (u: User) => { setSelectedUser(u); setModal('view'); };
  const openResetPw = (u: User) => { setSelectedUser(u); setFormData(f => ({ ...f, new_password: '' })); setModal('resetPw'); };
  const openDelete = (u: User) => { setSelectedUser(u); setModal('confirmDelete'); };
  const openReactivate = (u: User) => { setSelectedUser(u); setModal('confirmReactivate'); };

  const handleCreate = async () => {
    setSubmitting(true);
    try {
      await api.post('/v1/auth/users', {
        username: formData.username, password: formData.password,
        email: formData.email || null, role: formData.role,
        tenant_id: formData.tenant_id || null,
      });
      showToast(`${t.users.userCreated}: "${formData.username}"`);
      setModal(null); fetchUsers();
    } catch (err: any) {
      showToast(err.response?.data?.detail || 'Error', 'error');
    } finally { setSubmitting(false); }
  };

  const handleUpdate = async () => {
    if (!selectedUser) return;
    setSubmitting(true);
    try {
      const body: any = {};
      if (formData.email !== (selectedUser.email || '')) body.email = formData.email || null;
      if (formData.role !== selectedUser.role) body.role = formData.role;
      if (formData.tenant_id !== (selectedUser.tenant_id || '')) body.tenant_id = formData.tenant_id || null;
      if (Object.keys(body).length === 0) { setModal(null); return; }
      await api.patch(`/v1/auth/users/${selectedUser.id}`, body);
      showToast(`${t.users.userUpdated}: "${selectedUser.username}"`);
      setModal(null); fetchUsers();
    } catch (err: any) {
      showToast(err.response?.data?.detail || 'Error', 'error');
    } finally { setSubmitting(false); }
  };

  const handleResetPassword = async () => {
    if (!selectedUser) return;
    setSubmitting(true);
    try {
      await api.post(`/v1/auth/users/${selectedUser.id}/reset-password`, { new_password: formData.new_password });
      showToast(`${t.users.passwordReset} "${selectedUser.username}"`);
      setModal(null);
    } catch (err: any) {
      showToast(err.response?.data?.detail || 'Error', 'error');
    } finally { setSubmitting(false); }
  };

  const handleDeactivate = async () => {
    if (!selectedUser) return;
    setSubmitting(true);
    try {
      await api.delete(`/v1/auth/users/${selectedUser.id}`);
      showToast(`${t.users.userDeactivated}: "${selectedUser.username}"`);
      setModal(null); fetchUsers();
    } catch (err: any) {
      showToast(err.response?.data?.detail || 'Error', 'error');
    } finally { setSubmitting(false); }
  };

  const handleReactivate = async () => {
    if (!selectedUser) return;
    setSubmitting(true);
    try {
      await api.post(`/v1/auth/users/${selectedUser.id}/reactivate`);
      showToast(`${t.users.userReactivated}: "${selectedUser.username}"`);
      setModal(null); fetchUsers();
    } catch (err: any) {
      showToast(err.response?.data?.detail || 'Error', 'error');
    } finally { setSubmitting(false); }
  };

  if (!currentUser || (currentUser.role !== 'super_admin' && currentUser.role !== 'tenant_admin')) {
    return (
      <DashboardLayout title={t.users.title} meta={t.users.subtitle}>
        <div className={s.unauthorized}>
          <h1>{t.users.accessDenied}</h1>
          <p>{t.users.accessDeniedMsg}</p>
        </div>
      </DashboardLayout>
    );
  }

  const users = data?.users || [];
  const total = data?.total || 0;
  const totalPages = data?.total_pages || 1;
  const activeCount = users.filter(u => u.is_active).length;
  const adminCount = users.filter(u => u.role === 'super_admin' || u.role === 'tenant_admin').length;

  const renderPagination = () => {
    const pages: number[] = [];
    for (let i = Math.max(1, page - 2); i <= Math.min(totalPages, page + 2); i++) pages.push(i);
    return (
      <div className={s.pagination}>
        <span className={s.paginationInfo}>{t.users.showing} {users.length} {t.users.of} {total} {t.users.user.toLowerCase()}s</span>
        <div className={s.paginationControls}>
          <button className={s.pageBtn} disabled={page <= 1} onClick={() => setPage(p => p - 1)}>‹</button>
          {pages.map(p => (
            <button key={p} className={`${s.pageBtn} ${p === page ? s.pageBtnActive : ''}`} onClick={() => setPage(p)}>{p}</button>
          ))}
          <button className={s.pageBtn} disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>›</button>
        </div>
      </div>
    );
  };

  return (
    <DashboardLayout title={t.users.title} meta={t.users.subtitle}>
      <div className={s.container}>
        {/* Header Actions (Buttons only, Topbar handles Title) */}
        <header className={s.header}>
          <div className={s.headerLeft} />
          <div className={s.headerActions}>
            <button className={s.btnSecondary} onClick={fetchUsers}><IconRefresh size={14} /> {t.users.refresh}</button>
            <button className={s.btnPrimary} onClick={openCreate}><IconPlus size={14} /> {t.users.newUser}</button>
          </div>
        </header>

      {/* KPI Cards */}
      <div className={s.kpiGrid}>
        <div className={s.kpi}><div className={s.kpiLabel}>{t.users.totalUsers}</div><div className={s.kpiValue}>{total}</div><div className={s.kpiSub}>{t.users.acrossAllTenants}</div></div>
        <div className={s.kpi}><div className={s.kpiLabel}>{t.users.active}</div><div className={s.kpiValue}>{activeCount}</div><div className={s.kpiSub}>{t.users.currentlyEnabled}</div></div>
        <div className={s.kpi}><div className={s.kpiLabel}>{t.users.admins}</div><div className={s.kpiValue}>{adminCount}</div><div className={s.kpiSub}>{t.users.superTenantAdmins}</div></div>
        <div className={s.kpi}><div className={s.kpiLabel}>{t.users.tenants}</div><div className={s.kpiValue}>{tenants.length}</div><div className={s.kpiSub}>{t.users.activeOrganizations}</div></div>
      </div>

      {/* Toolbar */}
      <div className={s.toolbar}>
        <div className={s.searchBox}>
          <span className={s.searchIcon}><IconSearch size={14} /></span>
          <input className={s.searchInput} placeholder={t.users.searchPlaceholder} value={searchInput} onChange={e => setSearchInput(e.target.value)} />
        </div>
        <select className={s.filterSelect} value={roleFilter} onChange={e => { setRoleFilter(e.target.value); setPage(1); }}>
          <option value="all">{t.users.allRoles}</option>
          {ROLES.map(r => <option key={r} value={r}>{getRoleLabel(r)}</option>)}
        </select>
        <select className={s.filterSelect} value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1); }}>
          <option value="all">{t.users.allStatus}</option>
          <option value="active">{t.users.active}</option>
          <option value="inactive">{t.users.inactive}</option>
        </select>
      </div>

      {/* Table */}
      <div className={s.tableCard}>
        {loading ? (
          <div className={s.loadingState}><div className={s.spinner} /> {t.users.loading}</div>
        ) : error ? (
          <div className={s.errorState}>⚠ {error}</div>
        ) : users.length === 0 ? (
          <div className={s.emptyState}><div className={s.emptyIcon}><IconUsers size={36} /></div>{t.users.noUsersFound}</div>
        ) : (
          <>
            <div className={s.tableWrapper}>
              <table className={s.table}>
                <thead><tr>
                  <th>{t.users.user}</th><th>{t.users.role}</th><th>{t.users.tenant}</th><th>{t.users.status}</th><th>{t.users.lastLogin}</th><th>{t.users.created}</th><th style={{ textAlign: 'right' }}>{t.users.actions}</th>
                </tr></thead>
                <tbody>
                  {users.map(u => (
                    <tr key={u.id}>
                      <td><div className={s.userCell}>
                        <div className={`${s.avatar} ${avatarClass(u.role)}`}>{initials(u.username)}</div>
                        <div className={s.userInfo}><span className={s.userName}>{u.username}</span><span className={s.userEmail}>{u.email || t.users.noEmail}</span></div>
                      </div></td>
                      <td><span className={`${s.roleBadge} ${roleClass(u.role)}`}>{getRoleLabel(u.role)}</span></td>
                      <td>{u.tenant_id ? <span className={s.tenantCell}>{u.tenant_id}</span> : <span className={s.tenantSystem}>{t.users.system}</span>}</td>
                      <td><span className={`${s.statusBadge} ${u.is_active ? s.statusActive : s.statusInactive}`}><span className={s.statusDot} />{u.is_active ? t.users.active : t.users.inactive}</span></td>
                      <td className={s.dateCell}>{fmtDateTime(u.last_login_at)}</td>
                      <td className={s.dateCell}>{fmtDate(u.created_at)}</td>
                      <td><div className={s.actionsCell}>
                        <button className={s.actionBtn} title={t.users.viewDetails} onClick={() => openView(u)}><IconEye size={15} /></button>
                        <button className={s.actionBtn} title={t.users.editUser} onClick={() => openEdit(u)}><IconEdit size={15} /></button>
                        <button className={s.actionBtn} title={t.users.resetPassword} onClick={() => openResetPw(u)}><IconKey size={15} /></button>
                        {u.is_active
                          ? <button className={`${s.actionBtn} ${s.actionBtnDanger}`} title={t.users.deactivateUser} onClick={() => openDelete(u)} disabled={u.id === currentUser.id}><IconBan size={15} /></button>
                          : <button className={s.actionBtn} title={t.users.reactivateUser} onClick={() => openReactivate(u)}><IconCheck size={15} /></button>
                        }
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {renderPagination()}
          </>
        )}
      </div>

      {/* ── MODALS ── */}

      {/* Create User */}
      {modal === 'create' && (
        <div className={s.modalOverlay} onClick={() => setModal(null)}>
          <div className={s.modal} onClick={e => e.stopPropagation()}>
            <div className={s.modalHeader}><h2 className={s.modalTitle}>{t.users.createUser}</h2><button className={s.modalClose} onClick={() => setModal(null)}><IconX size={16} /></button></div>
            <div className={s.modalBody}>
              <div className={s.formGroup}><label className={s.formLabel}>{t.users.username} *</label><input className={s.formInput} placeholder="e.g. john_doe" value={formData.username} onChange={e => setFormData(f => ({ ...f, username: e.target.value }))} /></div>
              <div className={s.formGroup}><label className={s.formLabel}>{t.users.email}</label><input className={s.formInput} type="email" placeholder="john@company.com" value={formData.email} onChange={e => setFormData(f => ({ ...f, email: e.target.value }))} /></div>
              <div className={s.formGroup}><label className={s.formLabel}>{t.users.password} *</label><input className={s.formInput} type="password" placeholder={t.users.passwordHint} value={formData.password} onChange={e => setFormData(f => ({ ...f, password: e.target.value }))} /><div className={s.formHint}>{t.users.passwordHint}</div></div>
              <div className={s.formRow}>
                <div className={s.formGroup}><label className={s.formLabel}>{t.users.role}</label><select className={s.formSelect} value={formData.role} onChange={e => setFormData(f => ({ ...f, role: e.target.value }))}>{ROLES.map(r => <option key={r} value={r}>{getRoleLabel(r)}</option>)}</select></div>
                <div className={s.formGroup}><label className={s.formLabel}>{t.users.tenant}</label><select className={s.formSelect} value={formData.tenant_id} onChange={e => setFormData(f => ({ ...f, tenant_id: e.target.value }))}><option value="">{t.common.none} ({t.users.system})</option>{tenants.map(tn => <option key={tn} value={tn}>{tn}</option>)}</select><div className={s.formHint}>{t.users.tenantHint}</div></div>
              </div>
            </div>
            <div className={s.modalFooter}>
              <button className={s.btnSecondary} onClick={() => setModal(null)}>{t.users.cancel}</button>
              <button className={s.btnPrimary} onClick={handleCreate} disabled={submitting || !formData.username || !formData.password}>{submitting ? t.users.creating : t.users.createUser}</button>
            </div>
          </div>
        </div>
      )}

      {/* Edit User */}
      {modal === 'edit' && selectedUser && (
        <div className={s.modalOverlay} onClick={() => setModal(null)}>
          <div className={s.modal} onClick={e => e.stopPropagation()}>
            <div className={s.modalHeader}><h2 className={s.modalTitle}>{t.users.editUser} — {selectedUser.username}</h2><button className={s.modalClose} onClick={() => setModal(null)}><IconX size={16} /></button></div>
            <div className={s.modalBody}>
              <div className={s.formGroup}><label className={s.formLabel}>{t.users.email}</label><input className={s.formInput} type="email" value={formData.email} onChange={e => setFormData(f => ({ ...f, email: e.target.value }))} /></div>
              <div className={s.formRow}>
                <div className={s.formGroup}><label className={s.formLabel}>{t.users.role}</label><select className={s.formSelect} value={formData.role} onChange={e => setFormData(f => ({ ...f, role: e.target.value }))}>{ROLES.map(r => <option key={r} value={r}>{getRoleLabel(r)}</option>)}</select></div>
                <div className={s.formGroup}><label className={s.formLabel}>{t.users.tenant}</label><select className={s.formSelect} value={formData.tenant_id} onChange={e => setFormData(f => ({ ...f, tenant_id: e.target.value }))}><option value="">{t.common.none} ({t.users.system})</option>{tenants.map(tn => <option key={tn} value={tn}>{tn}</option>)}</select></div>
              </div>
            </div>
            <div className={s.modalFooter}>
              <button className={s.btnSecondary} onClick={() => setModal(null)}>{t.users.cancel}</button>
              <button className={s.btnPrimary} onClick={handleUpdate} disabled={submitting}>{submitting ? t.users.saving : t.users.save}</button>
            </div>
          </div>
        </div>
      )}

      {/* View User */}
      {modal === 'view' && selectedUser && (
        <div className={s.modalOverlay} onClick={() => setModal(null)}>
          <div className={s.modal} onClick={e => e.stopPropagation()}>
            <div className={s.modalHeader}><h2 className={s.modalTitle}>{t.users.userDetails}</h2><button className={s.modalClose} onClick={() => setModal(null)}><IconX size={16} /></button></div>
            <div className={s.modalBody}>
              <div className={s.detailGrid}>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.userId}</span><span className={`${s.detailValue} ${s.detailMono}`}>{selectedUser.id}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.username}</span><span className={s.detailValue}>{selectedUser.username}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.email}</span><span className={s.detailValue}>{selectedUser.email || '—'}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.role}</span><span className={`${s.roleBadge} ${roleClass(selectedUser.role)}`}>{getRoleLabel(selectedUser.role)}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.tenant}</span><span className={s.detailValue}>{selectedUser.tenant_id || t.users.system}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.status}</span><span className={`${s.statusBadge} ${selectedUser.is_active ? s.statusActive : s.statusInactive}`}><span className={s.statusDot} />{selectedUser.is_active ? t.users.active : t.users.inactive}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.created}</span><span className={s.detailValue}>{fmtDateTime(selectedUser.created_at)}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.lastLogin}</span><span className={s.detailValue}>{fmtDateTime(selectedUser.last_login_at)}</span></div>
                <div className={s.detailRow}><span className={s.detailLabel}>{t.users.lastActivity}</span><span className={s.detailValue}>{fmtDateTime(selectedUser.last_activity_at)}</span></div>
              </div>
            </div>
            <div className={s.modalFooter}>
              <button className={s.btnSecondary} onClick={() => { setModal(null); openEdit(selectedUser); }}><IconEdit size={14} /> {t.users.edit}</button>
              <button className={s.btnSecondary} onClick={() => { setModal(null); openResetPw(selectedUser); }}><IconKey size={14} /> {t.users.resetPassword}</button>
            </div>
          </div>
        </div>
      )}

      {/* Reset Password */}
      {modal === 'resetPw' && selectedUser && (
        <div className={s.modalOverlay} onClick={() => setModal(null)}>
          <div className={s.modal} onClick={e => e.stopPropagation()}>
            <div className={s.modalHeader}><h2 className={s.modalTitle}>{t.users.resetPassword} — {selectedUser.username}</h2><button className={s.modalClose} onClick={() => setModal(null)}><IconX size={16} /></button></div>
            <div className={s.modalBody}>
              <div className={s.formGroup}><label className={s.formLabel}>{t.users.newPassword} *</label><input className={s.formInput} type="password" placeholder={t.users.passwordHint} value={formData.new_password} onChange={e => setFormData(f => ({ ...f, new_password: e.target.value }))} /><div className={s.formHint}>{t.users.passwordLoginHint}</div></div>
            </div>
            <div className={s.modalFooter}>
              <button className={s.btnSecondary} onClick={() => setModal(null)}>{t.users.cancel}</button>
              <button className={s.btnPrimary} onClick={handleResetPassword} disabled={submitting || formData.new_password.length < 8}>{submitting ? t.users.resetting : t.users.resetPassword}</button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Deactivate */}
      {modal === 'confirmDelete' && selectedUser && (
        <div className={s.modalOverlay} onClick={() => setModal(null)}>
          <div className={s.modal} onClick={e => e.stopPropagation()}>
            <div className={s.modalHeader}><h2 className={s.modalTitle}>{t.users.confirmDeactivation}</h2><button className={s.modalClose} onClick={() => setModal(null)}><IconX size={16} /></button></div>
            <div className={s.modalBody}>
              <div className={s.confirmBody}>
                <div className={s.confirmIcon}><IconWarn size={40} /></div>
                <div className={s.confirmTitle}>{t.users.deactivateUser} &ldquo;{selectedUser.username}&rdquo;?</div>
                <div className={s.confirmText}>{t.users.deactivateMsg}</div>
              </div>
            </div>
            <div className={s.modalFooter}>
              <button className={s.btnSecondary} onClick={() => setModal(null)}>{t.users.cancel}</button>
              <button className={s.btnDanger} onClick={handleDeactivate} disabled={submitting}>{submitting ? t.users.deactivating : t.users.deactivateUser}</button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Reactivate */}
      {modal === 'confirmReactivate' && selectedUser && (
        <div className={s.modalOverlay} onClick={() => setModal(null)}>
          <div className={s.modal} onClick={e => e.stopPropagation()}>
            <div className={s.modalHeader}><h2 className={s.modalTitle}>{t.users.confirmReactivation}</h2><button className={s.modalClose} onClick={() => setModal(null)}><IconX size={16} /></button></div>
            <div className={s.modalBody}>
              <div className={s.confirmBody}>
                <div className={s.confirmIcon}><IconArrowLoop size={40} /></div>
                <div className={s.confirmTitle}>{t.users.reactivateUser} &ldquo;{selectedUser.username}&rdquo;?</div>
                <div className={s.confirmText}>{t.users.reactivateMsg}</div>
              </div>
            </div>
            <div className={s.modalFooter}>
              <button className={s.btnSecondary} onClick={() => setModal(null)}>{t.users.cancel}</button>
              <button className={s.btnSuccess} onClick={handleReactivate} disabled={submitting}>{submitting ? t.users.reactivating : t.users.reactivateUser}</button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && <div className={`${s.toast} ${toast.type === 'success' ? s.toastSuccess : s.toastError}`}>{toast.type === 'success' ? '✓' : '✕'} {toast.message}</div>}
    </div>
    </DashboardLayout>
  );
}
