"""
Prompt Management — Pydantic schemas.

Request/response contracts for the prompt template CMS.
"""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================
# VARIABLE DEFINITION
# ============================================================

class PromptVariableDef(BaseModel):
    """Definition of a single template variable."""
    name: str = Field(..., min_length=1, max_length=100, description="Variable name (alphanumeric + underscore)")
    type: str = Field(default="string", description="Variable type: string | number | boolean | json")
    required: bool = Field(default=True, description="Whether this variable must be provided")
    default: Optional[str] = Field(default=None, description="Default value if not provided")
    description: Optional[str] = Field(default=None, max_length=500, description="What this variable represents")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError("Variable name must be alphanumeric with underscores, starting with a letter or underscore")
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"string", "number", "boolean", "json"}
        if v not in allowed:
            raise ValueError(f"Type must be one of: {allowed}")
        return v


# ============================================================
# CREATE / UPDATE REQUESTS
# ============================================================

class PromptCreateRequest(BaseModel):
    """Request body for POST /v1/prompts."""
    name: str = Field(
        ..., min_length=2, max_length=200,
        description="Prompt identifier, e.g. 'summarize_report'",
    )
    content: str = Field(
        ..., min_length=10, max_length=100000,
        description="Prompt template text with {{variable}} placeholders",
    )
    tenant_id: Optional[str] = Field(
        default=None,
        description="Tenant scope. Auto-scoped for tenant_admin.",
    )
    description: Optional[str] = Field(
        default=None, max_length=1000,
        description="What this prompt does",
    )
    system_message: Optional[str] = Field(
        default=None, max_length=50000,
        description="System message prepended to the prompt",
    )
    variables: Optional[list[PromptVariableDef]] = Field(
        default=None,
        description="Variable definitions for the template",
    )
    model: Optional[str] = Field(
        default=None, max_length=100,
        description="Preferred model for this prompt",
    )
    max_tokens: Optional[int] = Field(
        default=None, ge=1, le=200000,
        description="Max output tokens",
    )
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0,
        description="Temperature setting",
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Categorization tags",
    )
    change_summary: Optional[str] = Field(
        default=None, max_length=500,
        description="What changed in this version (commit message)",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\-. ]*$", v):
            raise ValueError("Name must start with alphanumeric and contain only letters, numbers, underscores, hyphens, dots, spaces")
        return v


class PromptUpdateRequest(BaseModel):
    """Request body for PATCH /v1/prompts/{id} — creates a new version."""
    content: str = Field(
        ..., min_length=10, max_length=100000,
        description="Updated prompt template text",
    )
    description: Optional[str] = Field(default=None, max_length=1000)
    system_message: Optional[str] = Field(default=None, max_length=50000)
    variables: Optional[list[PromptVariableDef]] = Field(default=None)
    model: Optional[str] = Field(default=None, max_length=100)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=200000)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    tags: Optional[list[str]] = Field(default=None)
    change_summary: Optional[str] = Field(
        default=None, max_length=500,
        description="What changed in this version",
    )


class PromptRenderRequest(BaseModel):
    """Request body for POST /v1/prompts/{name}/render."""
    variables: dict = Field(
        default_factory=dict,
        description="Variable values to substitute into the template",
    )
    version: Optional[int] = Field(
        default=None,
        description="Specific version to render. Default = latest published.",
    )


# ============================================================
# RESPONSES
# ============================================================

class PromptResponse(BaseModel):
    """Full prompt template response."""
    id: str
    tenant_id: str
    name: str
    slug: str
    version: int
    content: str
    system_message: Optional[str] = None
    description: Optional[str] = None
    variables: Optional[list[PromptVariableDef]] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tags: Optional[list[str]] = None
    status: str
    is_published: bool
    published_at: Optional[datetime] = None
    published_by: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    parent_version_id: Optional[str] = None
    change_summary: Optional[str] = None
    usage_count: int = 0
    avg_tokens: Optional[float] = None

    model_config = {"from_attributes": True}


class PromptListResponse(BaseModel):
    """Paginated list of prompts."""
    prompts: list[PromptResponse]
    total: int
    page: int
    page_size: int


class PromptVersionListResponse(BaseModel):
    """All versions of a specific prompt."""
    name: str
    tenant_id: str
    versions: list[PromptResponse]
    total_versions: int
    published_version: Optional[int] = None


class PromptRenderResponse(BaseModel):
    """Rendered prompt text."""
    rendered: str
    system_message: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    version: int
    prompt_id: str


class PromptDiffResponse(BaseModel):
    """Diff between two versions of a prompt."""
    name: str
    version_a: int
    version_b: int
    content_a: str
    content_b: str
    variables_changed: bool
    system_message_changed: bool
