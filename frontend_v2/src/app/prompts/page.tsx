/** Prompt CMS page — NEW: Phase 2 prompt template management */
'use client';

import { useEffect, useState, useCallback } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import api from '@/lib/api';
import { useDashboard } from '@/lib/dashboard-context';
import { IconFile, IconPlus, IconCheck, IconPen } from '@/components/Icons';
import styles from './Prompts.module.css';

export default function PromptsPage() {
  const { selectedTenant } = useDashboard();
  const [templates, setTemplates] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  // Form State
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formContent, setFormContent] = useState('');
  const [formModel, setFormModel] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      let endpoint = '/v1/prompts/?published_only=false'; // Show all to see versions
      if (selectedTenant) {
        endpoint += `&tenant_id=${selectedTenant}`;
      }
      const res = await api.get(endpoint);
      const allPrompts = res.data.prompts || [];
      
      // Group by name and keep only the latest version of each prompt
      const grouped = new Map<string, any>();
      for (const p of allPrompts) {
        const existing = grouped.get(p.name);
        if (!existing || p.version > existing.version) {
          grouped.set(p.name, p);
        }
      }
      setTemplates(Array.from(grouped.values()));
    } catch (err: any) {
      if (err.response?.status !== 401) {
        console.error(err);
      }
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  }, [selectedTenant]); // Removed 'selected' from deps to avoid loop

  useEffect(() => {
    load();
  }, [load]);

  const viewTemplate = async (id: string) => {
    try {
      const res = await api.get(`/v1/prompts/${id}`);
      setSelected(res.data);
      setFormName(res.data.name || '');
      setFormDesc(res.data.description || '');
      setFormContent(res.data.content || '');
      setFormModel(res.data.model || '');
      setIsEditing(false);
      setIsCreating(false);
    } catch (e: any) {
      alert(e.response?.data?.detail || e.message);
    }
  };

  const startEdit = () => {
    if (!selected) return;
    setFormName(selected.name);
    setFormDesc(selected.description || '');
    setFormContent(selected.content || '');
    setFormModel(selected.model || '');
    setIsEditing(true);
    setIsCreating(false);
  };

  const startNew = () => {
    setFormName('');
    setFormDesc('');
    setFormContent('');
    setFormModel('gpt-4o-mini');
    setIsCreating(true);
    setIsEditing(false);
    setSelected(null);
  };

  const handleSave = async () => {
    try {
      const payload = {
        name: formName,
        description: formDesc,
        content: formContent,
        model: formModel,
        tenant_id: selectedTenant,
      };

      if (isCreating) {
        await api.post('/v1/prompts', payload);
      } else {
        await api.patch(`/v1/prompts/${selected.id}`, payload);
      }

      setIsEditing(false);
      setIsCreating(false);
      load();
      alert('Prompt saved successfully! New version created as Draft.');
    } catch (e: any) {
      alert(e.response?.data?.detail || e.message);
    }
  };

  const handlePublish = async () => {
    if (!selected) return;
    try {
      await api.post(`/v1/prompts/${selected.id}/publish`);
      load();
      viewTemplate(selected.id);
      alert('Prompt published to production!');
    } catch (e: any) {
      alert(e.response?.data?.detail || e.message);
    }
  };

  return (
    <DashboardLayout title="Prompt CMS" meta="Template versioning & management">
      <div className={styles.container}>
        {/* Sidebar */}
        <div className={styles.sidebar}>
          <div className={styles.sidebarHeader}>
            <div className={styles.sidebarTitle}>Templates</div>
            <button className={styles.newBtn} onClick={startNew}>
              <IconPlus size={14} /> New
            </button>
          </div>
          
          <div className={styles.list}>
            {loading ? (
              <div className={styles.loading}>Loading prompts...</div>
            ) : templates.length === 0 ? (
              <div className={styles.empty}>No templates found</div>
            ) : (
              templates.map((t) => (
                <div 
                  key={t.id} 
                  className={`${styles.item} ${selected?.id === t.id ? styles.itemActive : ''}`}
                  onClick={() => viewTemplate(t.id)}
                >
                  <div className={styles.itemHeader}>
                    <span className={styles.itemName}>{t.name}</span>
                    <span className={styles.itemVersion}>v{t.version}</span>
                  </div>
                  <div className={styles.itemFooter}>
                    <span className={`${styles.status} ${t.status === 'published' ? styles.statusPub : styles.statusDraft}`}>
                      {t.status}
                    </span>
                    <span className={styles.itemDate}>{new Date(t.updated_at).toLocaleDateString()}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Editor Area */}
        <div className={styles.editor}>
          {!selected && !isCreating ? (
            <div className={styles.placeholder}>
              <div className={styles.placeholderIcon}>
                <IconFile size={64} />
              </div>
              <h3>Prompt CMS</h3>
              <p>Select a template to view or manage versions</p>
            </div>
          ) : (
            <div className={styles.workspace}>
              <div className={styles.workspaceHeader}>
                <div className={styles.workspaceTitle}>
                  {isCreating ? 'Create New Prompt' : isEditing ? `Edit: ${selected?.name || ''}` : selected?.name || ''}
                </div>
                <div className={styles.workspaceActions}>
                  {!isEditing && !isCreating && (
                    <>
                      <button className={styles.actionBtn} onClick={startEdit}>
                        <IconPen size={14} style={{ marginRight: 6 }} /> Edit
                      </button>
                      {selected?.status !== 'published' && (
                        <button className={`${styles.actionBtn} ${styles.publishBtn}`} onClick={handlePublish}>
                          <IconCheck size={14} style={{ marginRight: 6 }} /> Publish
                        </button>
                      )}
                    </>
                  )}
                  {(isEditing || isCreating) && (
                    <>
                      <button className={styles.actionBtn} onClick={() => { setIsEditing(false); setIsCreating(false); }}>Cancel</button>
                      <button className={`${styles.actionBtn} ${styles.saveBtn}`} onClick={handleSave}>Save Version</button>
                    </>
                  )}
                </div>
              </div>

              <div className={styles.workspaceBody}>
                {/* Meta Inputs */}
                <div className={styles.formRow}>
                  <div className={styles.field}>
                    <label>Name</label>
                    <input 
                      type="text" 
                      value={formName} 
                      onChange={(e) => setFormName(e.target.value)}
                      disabled={!isEditing && !isCreating}
                      placeholder="e.g. summarize_report"
                    />
                  </div>
                  <div className={styles.field}>
                    <label>Preferred Model</label>
                    <input 
                      type="text" 
                      value={formModel} 
                      onChange={(e) => setFormModel(e.target.value)}
                      disabled={!isEditing && !isCreating}
                      placeholder="e.g. gpt-4o-mini"
                    />
                  </div>
                </div>

                <div className={styles.field}>
                  <label>Description</label>
                  <input 
                    type="text" 
                    value={formDesc} 
                    onChange={(e) => setFormDesc(e.target.value)}
                    disabled={!isEditing && !isCreating}
                    placeholder="Describe what this prompt does..."
                  />
                </div>

                <div className={styles.field} style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                  <label>Template Content (Jinja2 syntax: {'{{variable}}'})</label>
                  {isEditing || isCreating ? (
                    <textarea 
                      className={styles.textarea}
                      value={formContent}
                      onChange={(e) => setFormContent(e.target.value)}
                      placeholder="Write your prompt here..."
                    />
                  ) : (
                    <pre className={styles.preview}>
                      {selected.content}
                    </pre>
                  )}
                </div>

                {!isEditing && !isCreating && selected.variables && selected.variables.length > 0 && (
                  <div className={styles.variablesSection}>
                    <label>Variables Detected</label>
                    <div className={styles.varList}>
                      {selected.variables.map((v: any) => (
                        <span key={v.name || v} className={styles.varBadge}>
                          {`{{${v.name || v}}}`}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </DashboardLayout>
  );
}
