/** Forecasting page (M6) — replaces loadM6() */
'use client';
import { useEffect, useState, useRef } from 'react';
import DashboardLayout from '@/components/DashboardLayout/DashboardLayout';
import { useDashboard } from '@/lib/dashboard-context';
import api from '@/lib/api';

export default function ForecastingPage() {
  const { selectedTenant, setSelectedTenant } = useDashboard();
  const [risks, setRisks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [forecast, setForecast] = useState<any>(null);
  const [forecastLoading, setForecastLoading] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    setLoading(true); setError('');
    try {
      const res = await api.get('/v1/forecasting/budget-risk');
      let r = Array.isArray(res.data) ? res.data : (res.data.risks || []);
      if (selectedTenant) r = r.filter((x: any) => x.tenant_id === selectedTenant);
      setRisks(r);
    } catch (e: any) { setRisks([]); setError(e.response?.data?.detail || e.message); }
    finally { setLoading(false); }
  };

  const loadForecast = async (tenantId: string) => {
    setForecastLoading(true);
    try {
      const res = await api.get(`/v1/forecasting/forecast/${tenantId}`);
      setForecast(res.data);
    } catch (e: any) {
      setForecast(null);
    } finally {
      setForecastLoading(false);
    }
  };

  const handleRiskClick = (tenantId: string) => {
    setSelectedTenant(tenantId);
    loadForecast(tenantId);
    setTimeout(() => chartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);
  };

  const handleGenerateForecast = async () => {
    if (!selectedTenant) return;
    try {
      await api.post(`/v1/forecasting/forecast/${selectedTenant}`);
    } catch {}
    load();
    loadForecast(selectedTenant);
  };

  useEffect(() => { load(); }, [selectedTenant]);

  useEffect(() => {
    if (selectedTenant) loadForecast(selectedTenant);
    else setForecast(null);
  }, [selectedTenant]);

  const getUrgencyColor = (urgency: string) => {
    switch (urgency) {
      case 'critical': return { bg: 'rgba(239,68,68,0.10)', border: 'rgba(239,68,68,0.25)', text: 'var(--red, #ef4444)' };
      case 'warning':  return { bg: 'rgba(245,158,11,0.10)', border: 'rgba(245,158,11,0.25)', text: 'var(--yellow, #f59e0b)' };
      case 'watch':    return { bg: 'rgba(59,130,246,0.10)',  border: 'rgba(59,130,246,0.25)',  text: 'var(--acc, #3b82f6)' };
      default:         return { bg: 'rgba(245,158,11,0.10)', border: 'rgba(245,158,11,0.25)', text: 'var(--yellow, #f59e0b)' };
    }
  };

  return (
    <DashboardLayout title="Forecasting & Budget Risk" meta="Token usage forecasting">
      {loading && <div className="loading"><div className="spinner" /> Loading…</div>}
      {error && <div className="error-state" style={{ margin: 16, padding: 16, borderRadius: 'var(--r)', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--red)', fontSize: 12 }}>⚠ Failed to load forecasts: {error}</div>}
      {!loading && !error && (
        <div style={{ display: 'grid', gap: 16 }}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Budget Risk Assessment</div>
              {selectedTenant && <button className="btn btn-primary" style={{ fontSize: 11 }} onClick={handleGenerateForecast}>Generate Forecast</button>}
            </div>
            {risks.length === 0 ? <div style={{ padding: 12, fontSize: 12, color: 'var(--t2)' }}>No budget risks detected ✓</div> :
              risks.map((r, i) => {
                const colors = getUrgencyColor(r.urgency || 'warning');
                return (
                  <div
                    key={i}
                    onClick={() => handleRiskClick(r.tenant_id)}
                    style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '10px 12px', borderBottom: '1px solid var(--border)', fontSize: 12,
                      cursor: 'pointer', borderRadius: 6, transition: 'all 0.15s ease',
                      background: selectedTenant === r.tenant_id ? colors.bg : 'transparent',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = colors.bg; }}
                    onMouseLeave={e => { if (selectedTenant !== r.tenant_id) e.currentTarget.style.background = 'transparent'; }}
                  >
                    <div>
                      <div style={{ fontWeight: 600 }}>{r.tenant_id}</div>
                      <div style={{ fontSize: 10, color: 'var(--t2)' }}>{r.risk_type} · {r.days_until_exhaustion}d until exhaustion</div>
                    </div>
                    <button
                      className={`badge badge-${r.urgency || 'warning'}`}
                      style={{
                        cursor: 'pointer', border: `1px solid ${colors.border}`,
                        padding: '3px 10px', borderRadius: 6, fontWeight: 600,
                        fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em',
                        background: colors.bg, color: colors.text,
                        transition: 'all 0.15s ease',
                      }}
                      onClick={(e) => { e.stopPropagation(); handleRiskClick(r.tenant_id); }}
                      title={`Click to view forecast for ${r.tenant_id}`}
                    >
                      {r.urgency || 'warning'}
                    </button>
                  </div>
                );
              })
            }
          </div>

          {/* Forecast Detail Panel */}
          <div ref={chartRef}>
            {forecastLoading && (
              <div className="card" style={{ padding: 24, textAlign: 'center' }}>
                <div className="spinner" style={{ margin: '0 auto 8px' }} />
                <div style={{ fontSize: 12, color: 'var(--t2)' }}>Loading forecast data…</div>
              </div>
            )}
            {!forecastLoading && forecast && (
              <div className="card">
                <div className="card-header">
                  <div className="card-title">Forecast Detail — {selectedTenant}</div>
                </div>
                <div className="kpi-grid-2" style={{ marginBottom: 16 }}>
                  <div className="kpi" style={{ padding: '10px 12px' }}>
                    <div className="kpi-label">Predicted 30-Day Usage</div>
                    <div className="kpi-value" style={{ fontSize: 18 }}>
                      {forecast.summary?.monthly_likely_tokens 
                        ? `${Math.round(forecast.summary.monthly_likely_tokens).toLocaleString()} tokens` 
                        : (forecast.daily ? `${Math.round(forecast.daily.slice(0,30).reduce((acc:any, cur:any) => acc + (cur.likely_tokens||0), 0)).toLocaleString()} tokens` : 'N/A')}
                    </div>
                  </div>
                  <div className="kpi" style={{ padding: '10px 12px' }}>
                    <div className="kpi-label">Trend Slope</div>
                    <div className="kpi-value" style={{ fontSize: 18, color: (forecast.summary?.trend_slope || forecast.daily?.[0]?.trend_value) > 0 ? 'var(--amber)' : 'var(--green)' }}>
                      {(forecast.summary?.trend_slope !== undefined || forecast.daily?.[0]?.trend_value !== undefined)
                        ? (() => {
                            const val = forecast.summary?.trend_slope ?? forecast.daily[0].trend_value;
                            return `${val > 0 ? '+' : ''}${val.toFixed(1)} / day`;
                          })()
                        : 'N/A'}
                    </div>
                  </div>
                </div>
                {forecast.daily && forecast.daily.length > 0 && (
                  <div style={{ padding: '0 12px 12px' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: 'var(--t2)' }}>Forecast Trend ({forecast.daily.length} points)</div>
                    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 80 }}>
                      {forecast.daily.slice(-30).map((p: any, idx: number) => {
                        const max = Math.max(...forecast.daily.slice(-30).map((fp: any) => fp.likely_tokens || 0));
                        const val = p.likely_tokens || 0;
                        const heightPct = max > 0 ? (val / max) * 100 : 0;
                        return (
                          <div
                            key={idx}
                            title={`${p.forecast_date}: ${Math.round(val).toLocaleString()} tokens`}
                            style={{
                              flex: 1, borderRadius: '3px 3px 0 0',
                              height: `${Math.max(heightPct, 2)}%`,
                              background: `var(--acc, #3b82f6)`,
                              opacity: 0.5 + (idx / 30) * 0.5,
                              transition: 'height 0.3s ease',
                            }}
                          />
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}
