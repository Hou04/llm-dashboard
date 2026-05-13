/** AI Assistant page (M8) — replaces askM8() + m8Send() */
'use client';
import { useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';

interface ChatMessage { role: 'user' | 'assistant'; content: string; meta?: string; }

export default function AssistantPage() {
  const { period } = useDashboard();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  const send = async (preset?: string) => {
    const q = preset || input.trim();
    if (!q) return;
    setMessages(prev => [...prev, { role: 'user', content: q }]);
    if (!preset) setInput('');
    setLoading(true);
    try {
      const res = await api.get(`/v1/dashboard/assistant/ask?question=${encodeURIComponent(q)}&period_days=${period}`);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: res.data.answer || 'No response.',
        meta: `${res.data.tokens_used || 0} tokens · ${res.data.model_used || ''}${res.data.from_cache ? ' · cached' : ''}`,
      }]);
    } catch (e: any) {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Error: ' + e.message }]);
    } finally { setLoading(false); }
  };

  const presets = [
    'Which tenant has the highest cost this month?',
    'Are there any cost anomalies I should worry about?',
    'Compare model costs across all tenants',
    'What optimization opportunities exist?',
  ];

  return (
    <DashboardLayout title="AI Assistant" meta="Ask questions about your LLM usage">
      <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 500 }}>
        <div className="card-header">
          <div className="card-title">✦ AI Assistant</div>
          <span className="badge badge-blue">LLM-Powered</span>
        </div>

        {/* Chat messages */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {messages.length === 0 && (
            <div style={{ padding: 20, textAlign: 'center' }}>
              <div style={{ fontSize: 12, color: 'var(--t2)', marginBottom: 12 }}>Ask anything about your LLM usage, costs, or anomalies:</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
                {presets.map((p, i) => (
                  <button key={i} className="btn" style={{ fontSize: 11 }} onClick={() => send(p)}>{p}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} style={{
              padding: '10px 14px',
              borderRadius: 'var(--r)',
              background: m.role === 'user' ? 'var(--t1)' : 'var(--bg2)',
              color: m.role === 'user' ? '#fff' : 'var(--t1)',
              alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '80%',
              fontSize: 12,
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
            }}>
              {m.content}
              {m.meta && <div style={{ fontSize: 10, color: m.role === 'user' ? 'rgba(255,255,255,0.7)' : 'var(--t3)', marginTop: 4 }}>{m.meta}</div>}
            </div>
          ))}
          {loading && <div className="loading" style={{ padding: '8px 0' }}><div className="spinner" /> Thinking…</div>}
        </div>

        {/* Input */}
        <div style={{ display: 'flex', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && send()}
            placeholder="Ask about costs, anomalies, optimization…"
            style={{ flex: 1, padding: '8px 12px', borderRadius: 'var(--r)', border: '1px solid var(--border2)', background: 'var(--card2)', color: 'var(--t1)', fontSize: 12, fontFamily: 'inherit' }}
          />
          <button className="btn btn-primary" onClick={() => send()} disabled={loading}>Send</button>
        </div>
      </div>
    </DashboardLayout>
  );
}
