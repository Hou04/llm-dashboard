"""
ForecastEngine — Facebook Prophet-powered time-series forecasting.

UPGRADE FROM STL/LINEAR REGRESSION TO PROPHET
=============================================

Previous approach (v1):
  STL decomposition → linear regression on trend → symmetric σ√h intervals.

  Limitations:
  - Symmetric confidence intervals are unrealistic for cost data (always ≥ 0).
  - Linear trend can't capture growth that accelerates or slows.
  - No holiday/event awareness.
  - Seasonality was limited to a single 7-day period.

New approach (v2 — this file):
  Facebook Prophet — a Bayesian structural time series model developed
  at Meta for business forecasting.

  Advantages:
  - Asymmetric uncertainty intervals via MCMC-like sampling (yhat_lower
    and yhat_upper are NOT equidistant from yhat).
  - Automatically detects weekly and yearly seasonality.
  - Handles missing days, outliers, and growth changes robustly.
  - Piecewise-linear or logistic growth curves fit real usage patterns.
  - Interpretable components: trend, weekly_seasonality, etc.

  The engine retains the same DailyForecast / ForecastResult dataclass
  interface so no downstream code needs to change.

FUTURE SCALING PATH (v3 — not implemented):
  For 1,000+ tenants with high-frequency data:
  - Amazon DeepAR: probabilistic RNN that trains across all tenants
    simultaneously, learning cross-tenant patterns and producing native
    p5/p50/p95/p99 quantile forecasts.
  - Temporal Fusion Transformers (TFT): attention-based architecture
    that handles static covariates (tenant tier, agent type) alongside
    time-varying features. Requires GPU training but produces the most
    accurate probabilistic forecasts available.
  Both are PyTorch-based and would replace this file entirely.
"""

import math
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Minimum days of history needed before we forecast
MIN_HISTORY_DAYS = 21

# Prophet confidence interval width (95% = standard)
PROPHET_INTERVAL_WIDTH = 0.95

# Slope damping for optimistic scenario in fallback mode
OPTIMISTIC_SLOPE_DAMPING = 0.5

# Minimum residual std — prevents zero-width intervals for very consistent tenants
MIN_RESIDUAL_STD_RATIO = 0.05

# Floor: cost data can never go below 0
FLOOR_VALUE = 0.0


@dataclass
class DailyForecast:
    """Forecast for a single future day, all three scenarios."""
    forecast_date: date
    horizon_days: int          # 1 = tomorrow, 30 = 30 days from now

    # Token predictions
    likely_tokens: float
    pessimistic_tokens: float
    optimistic_tokens: float

    # Components (for interpretability / debugging)
    trend_value: float
    seasonal_multiplier: float
    confidence_half_width: float   # average of upper-lower spread


@dataclass
class ForecastResult:
    """Complete forecast output for one tenant (or one agent)."""
    tenant_id: str
    agent_id: str               # "*" = tenant aggregate
    generated_at: date
    horizon_days: int

    daily_forecasts: list[DailyForecast]

    # Trend statistics
    trend_slope: float
    trend_intercept: float
    residual_std: float
    history_days_used: int

    # Derived: monthly projection (sum of 30 daily forecasts)
    monthly_likely_tokens: float = 0.0
    monthly_pessimistic_tokens: float = 0.0
    monthly_optimistic_tokens: float = 0.0

    # Cost per token (derived from recent history)
    avg_cost_per_token: float = 0.0


def _try_import_prophet():
    """Lazy import of Prophet to avoid hard startup failure."""
    try:
        from prophet import Prophet
        return Prophet
    except ImportError:
        return None


class ForecastEngine:
    """
    Stateless forecasting engine.

    Uses Facebook Prophet when available, falls back to STL + linear
    regression if Prophet is not installed.

    All inputs are passed in, all outputs are returned.
    No database access, no async, fully unit-testable.
    """

    def __init__(self):
        self._Prophet = _try_import_prophet()
        if self._Prophet:
            logger.info("ForecastEngine: Prophet available — using ML forecasting")
        else:
            logger.warning(
                "ForecastEngine: Prophet not available — "
                "falling back to STL + linear regression"
            )

    def forecast(
        self,
        history: list[float],          # daily token totals, oldest first
        start_date: date,              # the date AFTER the last history point
        horizon_days: int = 30,
        avg_cost_per_token: float = 0.0,
        tenant_id: str = "",
        agent_id: str = "*",
    ) -> Optional[ForecastResult]:
        """
        Compute a multi-day forecast from historical daily token data.

        Uses Prophet if installed, otherwise falls back to STL.
        """
        if len(history) < MIN_HISTORY_DAYS:
            logger.info(
                "Insufficient history for forecast",
                extra={"tenant_id": tenant_id, "days": len(history)},
            )
            return None

        if self._Prophet:
            return self._forecast_prophet(
                history, start_date, horizon_days,
                avg_cost_per_token, tenant_id, agent_id,
            )
        else:
            return self._forecast_stl_fallback(
                history, start_date, horizon_days,
                avg_cost_per_token, tenant_id, agent_id,
            )

    # ============================================================
    # PROPHET FORECASTING (PRIMARY)
    # ============================================================

    def _forecast_prophet(
        self,
        history: list[float],
        start_date: date,
        horizon_days: int,
        avg_cost_per_token: float,
        tenant_id: str,
        agent_id: str,
    ) -> Optional[ForecastResult]:
        """
        Facebook Prophet forecast.

        Prophet expects a DataFrame with columns 'ds' (date) and 'y' (value).
        It returns yhat (likely), yhat_lower (optimistic — lower cost),
        and yhat_upper (pessimistic — higher cost) with ASYMMETRIC intervals.
        """
        import pandas as pd

        # Build the input DataFrame
        n = len(history)
        history_start = start_date - timedelta(days=n)
        dates = [history_start + timedelta(days=i) for i in range(n)]

        df = pd.DataFrame({
            "ds": pd.to_datetime(dates),
            "y": history,
        })

        # Floor at 0 — costs and tokens can never be negative
        df["y"] = df["y"].clip(lower=1.0)

        # Fit Prophet
        try:
            model = self._Prophet(
                interval_width=PROPHET_INTERVAL_WIDTH,
                weekly_seasonality=True,
                yearly_seasonality=False,  # most tenants don't have a year of data
                daily_seasonality=False,
                growth="linear",
                seasonality_mode="additive",
            )
            # Suppress verbose Stan output
            model.fit(df, iter=300)
        except Exception as e:
            logger.warning(f"Prophet fit failed: {e}, falling back to STL")
            return self._forecast_stl_fallback(
                history, start_date, horizon_days,
                avg_cost_per_token, tenant_id, agent_id,
            )

        # Generate future dataframe
        future = model.make_future_dataframe(periods=horizon_days)
        forecast_df = model.predict(future)

        # Extract only the future portion
        future_df = forecast_df.tail(horizon_days).reset_index(drop=True)

        # Extract trend slope from the model's trend component
        trend_component = forecast_df["trend"].values
        if len(trend_component) >= 2:
            # Slope = average daily change in trend over last 30 days of history
            recent_trend = trend_component[max(0, n - 30):n]
            if len(recent_trend) >= 2:
                slope = float(np.polyfit(range(len(recent_trend)), recent_trend, 1)[0])
            else:
                slope = 0.0
            intercept = float(trend_component[0])
        else:
            slope = 0.0
            intercept = float(np.mean(history))

        # Compute residual std from in-sample predictions
        in_sample = forecast_df.head(n)
        residuals = np.array(history) - in_sample["yhat"].values
        residual_std = float(np.std(residuals, ddof=1))
        mean_value = float(np.mean(history))
        residual_std = max(residual_std, mean_value * MIN_RESIDUAL_STD_RATIO)

        # Build DailyForecast objects
        daily_forecasts = []
        for idx in range(horizon_days):
            row = future_df.iloc[idx]
            forecast_date_val = row["ds"].date()
            h = idx + 1

            likely = max(FLOOR_VALUE, float(row["yhat"]))
            pessimistic = max(FLOOR_VALUE, float(row["yhat_upper"]))
            optimistic = max(FLOOR_VALUE, float(row["yhat_lower"]))

            # Extract components for interpretability
            trend_val = float(row["trend"]) if "trend" in row else likely
            weekly_val = float(row.get("weekly", 0.0))

            half_width = (pessimistic - optimistic) / 2.0

            daily_forecasts.append(DailyForecast(
                forecast_date=forecast_date_val,
                horizon_days=h,
                likely_tokens=round(likely, 1),
                pessimistic_tokens=round(pessimistic, 1),
                optimistic_tokens=round(optimistic, 1),
                trend_value=round(trend_val, 1),
                seasonal_multiplier=round(weekly_val, 1),
                confidence_half_width=round(half_width, 1),
            ))

        # Monthly totals
        monthly_likely = sum(d.likely_tokens for d in daily_forecasts)
        monthly_pessimistic = sum(d.pessimistic_tokens for d in daily_forecasts)
        monthly_optimistic = sum(d.optimistic_tokens for d in daily_forecasts)

        return ForecastResult(
            tenant_id=tenant_id,
            agent_id=agent_id,
            generated_at=start_date,
            horizon_days=horizon_days,
            daily_forecasts=daily_forecasts,
            trend_slope=round(slope, 2),
            trend_intercept=round(intercept, 2),
            residual_std=round(residual_std, 2),
            history_days_used=n,
            monthly_likely_tokens=round(monthly_likely, 1),
            monthly_pessimistic_tokens=round(monthly_pessimistic, 1),
            monthly_optimistic_tokens=round(monthly_optimistic, 1),
            avg_cost_per_token=avg_cost_per_token,
        )

    # ============================================================
    # STL + LINEAR REGRESSION FALLBACK
    # ============================================================

    def _forecast_stl_fallback(
        self,
        history: list[float],
        start_date: date,
        horizon_days: int,
        avg_cost_per_token: float,
        tenant_id: str,
        agent_id: str,
    ) -> Optional[ForecastResult]:
        """
        Original STL decomposition + linear regression approach.
        Used as fallback when Prophet is not installed.
        """
        from statsmodels.tsa.seasonal import STL

        series = np.array(history, dtype=float)
        series = np.where(series <= 0, 1.0, series)

        # STL decomposition
        stl = STL(
            series,
            period=7,
            seasonal=7,
            trend=min(15, len(series) - 1 if len(series) % 2 == 0 else len(series)),
            robust=True,
        )
        result = stl.fit()
        trend = result.trend.tolist()
        seasonal = result.seasonal.tolist()
        residual = result.resid.tolist()

        # Linear regression on trend
        recent = trend[-30:]
        t = np.arange(len(trend) - len(recent), len(trend))
        coeffs = np.polyfit(t, recent, deg=1)
        slope, intercept = float(coeffs[0]), float(coeffs[1])

        # Seasonal by weekday
        history_start = start_date - timedelta(days=len(history))
        by_weekday: dict[int, list[float]] = {i: [] for i in range(7)}
        for i, s_val in enumerate(seasonal):
            d = history_start + timedelta(days=i)
            by_weekday[d.weekday()].append(s_val)
        seasonal_by_weekday = {
            wd: float(np.mean(vals)) if vals else 0.0
            for wd, vals in by_weekday.items()
        }

        # Residual std
        residual_arr = np.array(residual)
        residual_std = float(np.std(residual_arr, ddof=1))
        mean_value = float(np.mean(series))
        residual_std = max(residual_std, mean_value * MIN_RESIDUAL_STD_RATIO)

        # Generate forecasts
        n = len(history)
        daily_forecasts = []

        for h in range(1, horizon_days + 1):
            forecast_date_val = start_date + timedelta(days=h - 1)
            weekday = forecast_date_val.weekday()

            trend_value = intercept + slope * (n + h)
            seasonal_mult = seasonal_by_weekday[weekday]
            half_width = residual_std * math.sqrt(h)

            likely = max(0.0, trend_value + seasonal_mult)
            pessimistic = max(0.0, likely + half_width)

            trend_optimistic = intercept + (slope * OPTIMISTIC_SLOPE_DAMPING) * (n + h)
            optimistic = max(0.0, trend_optimistic + seasonal_mult - half_width * 0.5)

            daily_forecasts.append(DailyForecast(
                forecast_date=forecast_date_val,
                horizon_days=h,
                likely_tokens=round(likely, 1),
                pessimistic_tokens=round(pessimistic, 1),
                optimistic_tokens=round(optimistic, 1),
                trend_value=round(trend_value, 1),
                seasonal_multiplier=round(seasonal_mult, 1),
                confidence_half_width=round(half_width, 1),
            ))

        monthly_likely = sum(d.likely_tokens for d in daily_forecasts)
        monthly_pessimistic = sum(d.pessimistic_tokens for d in daily_forecasts)
        monthly_optimistic = sum(d.optimistic_tokens for d in daily_forecasts)

        return ForecastResult(
            tenant_id=tenant_id,
            agent_id=agent_id,
            generated_at=start_date,
            horizon_days=horizon_days,
            daily_forecasts=daily_forecasts,
            trend_slope=round(slope, 2),
            trend_intercept=round(intercept, 2),
            residual_std=round(residual_std, 2),
            history_days_used=len(history),
            monthly_likely_tokens=round(monthly_likely, 1),
            monthly_pessimistic_tokens=round(monthly_pessimistic, 1),
            monthly_optimistic_tokens=round(monthly_optimistic, 1),
            avg_cost_per_token=avg_cost_per_token,
        )

    # ============================================================
    # BUDGET RISK CALCULATION (unchanged)
    # ============================================================

    def compute_budget_risk(
        self,
        forecast: ForecastResult,
        daily_token_limit: Optional[float],
        monthly_budget_usd: Optional[float],
        current_monthly_tokens: float,
        current_monthly_cost_usd: float,
    ) -> list[dict]:
        """
        Check if the forecast predicts hitting a governance limit.

        Returns a list of risk dicts (may be empty if no risk detected).
        """
        risks = []

        if daily_token_limit and daily_token_limit > 0:
            for d in forecast.daily_forecasts:
                if d.likely_tokens >= daily_token_limit:
                    risks.append({
                        "risk_type": "token_limit",
                        "days_until_exhaustion": d.horizon_days,
                        "exhaustion_date": d.forecast_date,
                        "forecasted_value_at_exhaustion": d.likely_tokens,
                        "governance_limit": daily_token_limit,
                        "pct_of_limit_today": round(
                            current_monthly_tokens / daily_token_limit * 100, 1
                        ),
                    })
                    break

        if monthly_budget_usd and monthly_budget_usd > 0 and forecast.avg_cost_per_token > 0:
            cumulative_cost = current_monthly_cost_usd
            for d in forecast.daily_forecasts:
                daily_cost = d.likely_tokens * forecast.avg_cost_per_token
                cumulative_cost += daily_cost
                if cumulative_cost >= monthly_budget_usd:
                    risks.append({
                        "risk_type": "cost_budget",
                        "days_until_exhaustion": d.horizon_days,
                        "exhaustion_date": d.forecast_date,
                        "forecasted_value_at_exhaustion": round(cumulative_cost, 4),
                        "governance_limit": monthly_budget_usd,
                        "pct_of_limit_today": round(
                            current_monthly_cost_usd / monthly_budget_usd * 100, 1
                        ),
                    })
                    break

        return risks