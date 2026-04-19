import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class BaselineResponse(BaseModel):
    tenant_id: str
    daily_mean: float
    daily_std_dev: float
    daily_min: float
    daily_max: float
    call_count_mean: float
    error_rate_mean: float
    sample_days: int
    computed_at: datetime
    isolation_forest_threshold: float
    cusum_pos: float
    cusum_neg: float
    model_config = {"from_attributes": True}


class AnomalyResponse(BaseModel):
    id: uuid.UUID
    tenant_id: str
    anomaly_type: str
    severity: str
    detector_votes: str
    vote_count: int
    stl_residual_zscore: Optional[float] = None
    isolation_score: Optional[float] = None
    cusum_value: Optional[float] = None
    observed_value: float
    baseline_mean: float
    description: Optional[str] = None
    resolved: bool
    detected_at: datetime
    model_config = {"from_attributes": True}


class AnomalyListResponse(BaseModel):
    tenant_id: Optional[str] = None
    anomalies: list[AnomalyResponse]
    total: int


class DetectionCheckResponse(BaseModel):
    tenant_id: str
    fired: bool
    severity: str
    anomaly_type: str
    vote_count: int
    detector_names: list[str]
    stl_z: Optional[float] = None
    isolation_score: Optional[float] = None
    cusum_value: Optional[float] = None
    anomaly_id: Optional[str] = None