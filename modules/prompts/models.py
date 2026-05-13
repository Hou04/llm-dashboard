"""
Prompt Template Models — versioned prompt CMS for the LLM Gateway.

Design:
  - Each prompt has a unique (tenant_id, name) pair
  - Multiple versions exist per prompt — only ONE is published at a time
  - Publishing sets is_published=True on the target version and False on the rest
  - Rollback = re-publish a previous version
  - Variables are declared in the template as {{variable_name}} Jinja2-style
  - The variables[] JSONB column stores metadata about each variable (type, default, required)

Table: llm_prompt_templates
  One row per version of a prompt. Immutable once created — edits create new versions.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class PromptStatus(str, PyEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class LLMPromptTemplate(Base):
    """
    Versioned prompt template.

    Represents a single version of a prompt template. Prompts are identified
    by (tenant_id, name). Each edit creates a new version. Only one version
    per (tenant_id, name) can be published at a time.

    Variables in the template use Jinja2 syntax: {{variable_name}}.
    The `variables` JSONB column documents each variable:
      [
        {"name": "report_data", "type": "string", "required": true, "default": null},
        {"name": "language", "type": "string", "required": false, "default": "English"},
      ]

    Usage via SDK:
      template = client.prompts.get("summarize_report", version="latest")
      rendered = template.render(report_data="...", language="French")
    """

    __tablename__ = "llm_prompt_templates"

    # ---- Identity ----
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="Tenant that owns this prompt template",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False,
        comment="Prompt identifier, e.g. 'summarize_report'. Unique per tenant.",
    )
    slug: Mapped[str] = mapped_column(
        String(200), nullable=False,
        comment="URL-safe version of name, e.g. 'summarize-report'",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="Auto-incrementing version number per (tenant_id, name)",
    )

    # ---- Content ----
    content: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="The prompt template text with {{variable}} placeholders",
    )
    system_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Optional system message prepended to the prompt",
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(1000), nullable=True,
        comment="Human-readable description of what this prompt does",
    )

    # ---- Variables ----
    variables: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Variable definitions: [{name, type, required, default, description}]",
    )

    # ---- Configuration ----
    model: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Preferred model for this prompt (e.g., 'gpt-4o-mini')",
    )
    max_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Max output tokens for this prompt",
    )
    temperature: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True,
        comment="Temperature setting (0.0-2.0)",
    )
    tags: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Categorization tags, e.g. ['finance', 'report', 'summarization']",
    )

    # ---- Lifecycle ----
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PromptStatus.DRAFT.value,
        comment="Prompt status: draft | published | archived",
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="True if this version is the active/published one",
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="When this version was published",
    )
    published_by: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True,
        comment="User ID who published this version",
    )

    # ---- Authorship ----
    created_by: Mapped[str] = mapped_column(
        String(36), nullable=False,
        comment="User ID who created this version",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ---- Versioning ----
    parent_version_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True,
        comment="ID of the previous version this was derived from",
    )
    change_summary: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True,
        comment="What changed in this version (commit message)",
    )

    # ---- Usage Metrics ----
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Number of times this prompt version has been used",
    )
    avg_tokens: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True,
        comment="Average token count when this prompt is rendered",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_prompt_tenant_name_version"),
        Index("ix_prompt_tenant_name", "tenant_id", "name"),
        Index("ix_prompt_published", "tenant_id", "name", "is_published"),
        Index("ix_prompt_slug", "tenant_id", "slug"),
    )

    def __repr__(self) -> str:
        pub = "✓" if self.is_published else "○"
        return f"<LLMPromptTemplate {self.name} v{self.version} [{pub}] tenant={self.tenant_id}>"

    @property
    def variable_names(self) -> list[str]:
        """Extract variable names from the variables definition."""
        if not self.variables:
            return []
        return [v.get("name", "") for v in self.variables if isinstance(v, dict)]

    def render(self, **kwargs) -> str:
        """
        Render the template by substituting variables.
        Uses simple string replacement (Jinja2-compatible syntax).

        Args:
            **kwargs: Variable values to substitute.

        Returns:
            Rendered prompt text.

        Raises:
            ValueError: If a required variable is missing.
        """
        rendered = self.content
        for var_def in (self.variables or []):
            name = var_def.get("name", "")
            required = var_def.get("required", False)
            default = var_def.get("default")

            value = kwargs.get(name, default)
            if value is None and required:
                raise ValueError(f"Required variable '{name}' not provided")

            if value is not None:
                rendered = rendered.replace("{{" + name + "}}", str(value))

        return rendered
