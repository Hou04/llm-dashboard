"""
PromptRepository — all database operations for llm_prompt_templates.

Design follows the same repository pattern as LogRepository:
- Every method receives an AsyncSession
- Methods never commit — the caller controls transactions
- All queries use explicit column selection where possible
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from modules.prompts.models import LLMPromptTemplate, PromptStatus


def _slugify(name: str) -> str:
    """Convert a prompt name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


class PromptRepository:
    """Repository for llm_prompt_templates."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================
    # WRITES
    # ================================================================

    async def create(self, template: LLMPromptTemplate) -> LLMPromptTemplate:
        """Insert a new prompt template version."""
        self.session.add(template)
        await self.session.flush()
        await self.session.refresh(template)
        return template

    async def update_published_status(
        self,
        tenant_id: str,
        name: str,
        version_to_publish: int,
        published_by: str,
    ) -> Optional[LLMPromptTemplate]:
        """
        Publish a specific version and unpublish all others for (tenant_id, name).

        This is an atomic operation:
        1. Set is_published=False for ALL versions of this prompt
        2. Set is_published=True for the target version
        3. Update status and timestamps

        Returns the newly published template, or None if not found.
        """
        # Step 1: Unpublish all versions
        await self.session.execute(
            update(LLMPromptTemplate)
            .where(
                LLMPromptTemplate.tenant_id == tenant_id,
                LLMPromptTemplate.name == name,
            )
            .values(
                is_published=False,
                status=PromptStatus.ARCHIVED.value,
            )
        )

        # Step 2: Publish the target version
        result = await self.session.execute(
            select(LLMPromptTemplate).where(
                LLMPromptTemplate.tenant_id == tenant_id,
                LLMPromptTemplate.name == name,
                LLMPromptTemplate.version == version_to_publish,
            )
        )
        template = result.scalar_one_or_none()
        if template is None:
            return None

        template.is_published = True
        template.status = PromptStatus.PUBLISHED.value
        template.published_at = datetime.now(timezone.utc)
        template.published_by = published_by
        await self.session.flush()
        return template

    async def increment_usage(self, template_id: str) -> None:
        """Increment usage_count for a template."""
        await self.session.execute(
            update(LLMPromptTemplate)
            .where(LLMPromptTemplate.id == template_id)
            .values(usage_count=LLMPromptTemplate.usage_count + 1)
        )

    # ================================================================
    # READS — single record
    # ================================================================

    async def get_by_id(self, template_id: str) -> Optional[LLMPromptTemplate]:
        """Fetch a single template by ID."""
        result = await self.session.execute(
            select(LLMPromptTemplate).where(LLMPromptTemplate.id == template_id)
        )
        return result.scalar_one_or_none()

    async def get_published(
        self, tenant_id: str, name: str
    ) -> Optional[LLMPromptTemplate]:
        """Get the currently published version of a prompt."""
        result = await self.session.execute(
            select(LLMPromptTemplate).where(
                LLMPromptTemplate.tenant_id == tenant_id,
                LLMPromptTemplate.name == name,
                LLMPromptTemplate.is_published == True,
            )
        )
        return result.scalar_one_or_none()

    async def get_version(
        self, tenant_id: str, name: str, version: int
    ) -> Optional[LLMPromptTemplate]:
        """Get a specific version of a prompt."""
        result = await self.session.execute(
            select(LLMPromptTemplate).where(
                LLMPromptTemplate.tenant_id == tenant_id,
                LLMPromptTemplate.name == name,
                LLMPromptTemplate.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_version_number(
        self, tenant_id: str, name: str
    ) -> int:
        """Get the highest version number for a prompt. Returns 0 if none exist."""
        result = await self.session.execute(
            select(func.coalesce(func.max(LLMPromptTemplate.version), 0)).where(
                LLMPromptTemplate.tenant_id == tenant_id,
                LLMPromptTemplate.name == name,
            )
        )
        return int(result.scalar() or 0)

    # ================================================================
    # READS — collections
    # ================================================================

    async def list_prompts(
        self,
        tenant_id: Optional[str] = None,
        tag: Optional[str] = None,
        search: Optional[str] = None,
        published_only: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> list[LLMPromptTemplate]:
        """
        List prompt templates with filtering and pagination.

        When published_only=True, returns only the currently published version
        of each prompt. Otherwise returns the latest version of each.
        """
        conditions = []
        if tenant_id:
            conditions.append(LLMPromptTemplate.tenant_id == tenant_id)
        if published_only:
            conditions.append(LLMPromptTemplate.is_published == True)

        if search:
            search_pattern = f"%{search}%"
            from sqlalchemy import or_
            conditions.append(
                or_(
                    LLMPromptTemplate.name.ilike(search_pattern),
                    LLMPromptTemplate.description.ilike(search_pattern),
                    LLMPromptTemplate.content.ilike(search_pattern),
                )
            )

        query = select(LLMPromptTemplate)
        if conditions:
            query = query.where(and_(*conditions))

        query = (
            query
            .order_by(
                LLMPromptTemplate.name.asc(),
                LLMPromptTemplate.version.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_versions(
        self, tenant_id: str, name: str
    ) -> list[LLMPromptTemplate]:
        """Get all versions of a specific prompt, newest first."""
        result = await self.session.execute(
            select(LLMPromptTemplate)
            .where(
                LLMPromptTemplate.tenant_id == tenant_id,
                LLMPromptTemplate.name == name,
            )
            .order_by(LLMPromptTemplate.version.desc())
        )
        return list(result.scalars().all())

    async def count_prompts(
        self,
        tenant_id: Optional[str] = None,
        published_only: bool = False,
        search: Optional[str] = None,
    ) -> int:
        """Count matching prompts for pagination."""
        conditions = []
        if tenant_id:
            conditions.append(LLMPromptTemplate.tenant_id == tenant_id)
        if published_only:
            conditions.append(LLMPromptTemplate.is_published == True)
        if search:
            search_pattern = f"%{search}%"
            from sqlalchemy import or_
            conditions.append(
                or_(
                    LLMPromptTemplate.name.ilike(search_pattern),
                    LLMPromptTemplate.description.ilike(search_pattern),
                )
            )

        query = select(func.count(LLMPromptTemplate.id))
        if conditions:
            query = query.where(and_(*conditions))

        result = await self.session.execute(query)
        return int(result.scalar() or 0)

    async def get_unique_prompt_names(
        self, tenant_id: str
    ) -> list[str]:
        """Get all unique prompt names for a tenant."""
        result = await self.session.execute(
            select(LLMPromptTemplate.name)
            .where(LLMPromptTemplate.tenant_id == tenant_id)
            .distinct()
            .order_by(LLMPromptTemplate.name.asc())
        )
        return [row[0] for row in result.all()]
