"""
PromptService — business logic for prompt template management.

Orchestrates:
- Version creation (each edit = new immutable version)
- Publishing (only one published version per prompt name)
- Rollback (re-publish a previous version)
- Template rendering with variable substitution
- Variable extraction from template content
"""

import re
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules.prompts.models import LLMPromptTemplate, PromptStatus
from modules.prompts.repository import PromptRepository, _slugify
from modules.prompts.schemas import PromptCreateRequest, PromptUpdateRequest

logger = logging.getLogger(__name__)


class PromptService:
    """Service layer for prompt template management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PromptRepository(session)

    # ================================================================
    # PUBLIC — CRUD
    # ================================================================

    async def create_prompt(
        self,
        request: PromptCreateRequest,
        tenant_id: str,
        user_id: str,
    ) -> LLMPromptTemplate:
        """
        Create a new prompt template (version 1).

        If the prompt name already exists for this tenant, this creates
        a new version instead. The caller should use update_prompt() for
        explicit version bumps — this method handles the edge case gracefully.

        Auto-detects variables from {{variable}} syntax in the content
        if no variables are explicitly provided.
        """
        # Check if this prompt name already exists
        current_version = await self.repo.get_latest_version_number(tenant_id, request.name)
        version = current_version + 1

        # Auto-detect variables if not provided
        variables = None
        if request.variables:
            variables = [v.model_dump() for v in request.variables]
        else:
            detected = self._extract_variables(request.content)
            if detected:
                variables = [{"name": v, "type": "string", "required": True, "default": None} for v in detected]

        slug = _slugify(request.name)

        template = LLMPromptTemplate(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=request.name,
            slug=slug,
            version=version,
            content=request.content,
            system_message=request.system_message,
            description=request.description,
            variables=variables,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            tags=request.tags,
            status=PromptStatus.DRAFT.value,
            is_published=False,
            created_by=user_id,
            change_summary=request.change_summary or f"Initial version" if version == 1 else f"Version {version}",
            parent_version_id=None,
        )

        # If this is the first version and no other exists, auto-publish
        if version == 1:
            template.is_published = True
            template.status = PromptStatus.PUBLISHED.value
            template.published_at = datetime.now(timezone.utc)
            template.published_by = user_id

        saved = await self.repo.create(template)
        await self.session.commit()

        logger.info(
            f"prompt.created name={request.name} v{version} "
            f"tenant={tenant_id} published={template.is_published}"
        )
        return saved

    async def update_prompt(
        self,
        prompt_id: str,
        request: PromptUpdateRequest,
        user_id: str,
    ) -> Optional[LLMPromptTemplate]:
        """
        Create a new version of an existing prompt.

        Does NOT modify the existing version — creates a new immutable
        version with the updated content. The new version starts as a draft.

        Returns the new version, or None if the source prompt was not found.
        """
        # Get the source prompt
        source = await self.repo.get_by_id(prompt_id)
        if source is None:
            return None

        # Get next version number
        next_version = await self.repo.get_latest_version_number(
            source.tenant_id, source.name
        ) + 1

        # Auto-detect variables if not provided
        variables = None
        if request.variables:
            variables = [v.model_dump() for v in request.variables]
        else:
            detected = self._extract_variables(request.content)
            if detected:
                variables = [{"name": v, "type": "string", "required": True, "default": None} for v in detected]

        new_template = LLMPromptTemplate(
            id=str(uuid.uuid4()),
            tenant_id=source.tenant_id,
            name=source.name,
            slug=source.slug,
            version=next_version,
            content=request.content,
            system_message=request.system_message if request.system_message is not None else source.system_message,
            description=request.description if request.description is not None else source.description,
            variables=variables if variables is not None else source.variables,
            model=request.model if request.model is not None else source.model,
            max_tokens=request.max_tokens if request.max_tokens is not None else source.max_tokens,
            temperature=request.temperature if request.temperature is not None else source.temperature,
            tags=request.tags if request.tags is not None else source.tags,
            status=PromptStatus.DRAFT.value,
            is_published=False,
            created_by=user_id,
            parent_version_id=source.id,
            change_summary=request.change_summary or f"Updated from v{source.version}",
        )

        saved = await self.repo.create(new_template)
        await self.session.commit()

        logger.info(
            f"prompt.updated name={source.name} v{source.version} → v{next_version} "
            f"tenant={source.tenant_id}"
        )
        return saved

    async def publish(
        self,
        prompt_id: str,
        user_id: str,
    ) -> Optional[LLMPromptTemplate]:
        """
        Publish a specific prompt version.

        Unpublishes all other versions of the same prompt and sets
        this version as the active/published one.
        """
        template = await self.repo.get_by_id(prompt_id)
        if template is None:
            return None

        published = await self.repo.update_published_status(
            tenant_id=template.tenant_id,
            name=template.name,
            version_to_publish=template.version,
            published_by=user_id,
        )
        await self.session.commit()

        if published:
            logger.info(
                f"prompt.published name={template.name} v{template.version} "
                f"tenant={template.tenant_id} by={user_id}"
            )
        return published

    async def rollback(
        self,
        tenant_id: str,
        name: str,
        target_version: int,
        user_id: str,
    ) -> Optional[LLMPromptTemplate]:
        """
        Rollback to a previous version of a prompt.

        This simply re-publishes the target version — it does NOT
        create a new version. The old published version becomes archived.
        """
        published = await self.repo.update_published_status(
            tenant_id=tenant_id,
            name=name,
            version_to_publish=target_version,
            published_by=user_id,
        )
        await self.session.commit()

        if published:
            logger.info(
                f"prompt.rollback name={name} → v{target_version} "
                f"tenant={tenant_id} by={user_id}"
            )
        return published

    # ================================================================
    # PUBLIC — RETRIEVAL
    # ================================================================

    async def get_prompt(
        self,
        tenant_id: str,
        name: str,
        version: Optional[int] = None,
    ) -> Optional[LLMPromptTemplate]:
        """
        Get a prompt template.

        If version is specified, returns that exact version.
        Otherwise returns the currently published version.
        This is the SDK entry point: client.prompts.get("name", version="latest")
        """
        if version is not None:
            return await self.repo.get_version(tenant_id, name, version)
        return await self.repo.get_published(tenant_id, name)

    async def get_by_id(self, prompt_id: str) -> Optional[LLMPromptTemplate]:
        """Get a prompt by its ID."""
        return await self.repo.get_by_id(prompt_id)

    async def list_prompts(
        self,
        tenant_id: Optional[str] = None,
        search: Optional[str] = None,
        published_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[LLMPromptTemplate], int]:
        """List prompts with pagination. Returns (prompts, total_count)."""
        prompts = await self.repo.list_prompts(
            tenant_id=tenant_id,
            search=search,
            published_only=published_only,
            page=page,
            page_size=page_size,
        )
        total = await self.repo.count_prompts(
            tenant_id=tenant_id,
            published_only=published_only,
            search=search,
        )
        return prompts, total

    async def list_versions(
        self,
        tenant_id: str,
        name: str,
    ) -> list[LLMPromptTemplate]:
        """Get all versions of a prompt."""
        return await self.repo.list_versions(tenant_id, name)

    # ================================================================
    # PUBLIC — RENDERING
    # ================================================================

    async def render(
        self,
        tenant_id: str,
        name: str,
        variables: dict,
        version: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Render a prompt template with variable substitution.

        Returns a dict with the rendered text, config, and metadata.
        Returns None if the prompt is not found.
        """
        template = await self.get_prompt(tenant_id, name, version)
        if template is None:
            return None

        try:
            rendered = template.render(**variables)
        except ValueError as e:
            raise ValueError(f"Template render failed: {e}")

        # Increment usage (best-effort)
        try:
            await self.repo.increment_usage(template.id)
            await self.session.commit()
        except Exception:
            pass

        return {
            "rendered": rendered,
            "system_message": template.system_message,
            "model": template.model,
            "max_tokens": template.max_tokens,
            "temperature": template.temperature,
            "version": template.version,
            "prompt_id": template.id,
        }

    # ================================================================
    # PUBLIC — DIFF
    # ================================================================

    async def diff_versions(
        self,
        tenant_id: str,
        name: str,
        version_a: int,
        version_b: int,
    ) -> Optional[dict]:
        """Compare two versions of a prompt."""
        a = await self.repo.get_version(tenant_id, name, version_a)
        b = await self.repo.get_version(tenant_id, name, version_b)

        if a is None or b is None:
            return None

        return {
            "name": name,
            "version_a": version_a,
            "version_b": version_b,
            "content_a": a.content,
            "content_b": b.content,
            "variables_changed": (a.variables or []) != (b.variables or []),
            "system_message_changed": (a.system_message or "") != (b.system_message or ""),
        }

    # ================================================================
    # PRIVATE — HELPERS
    # ================================================================

    @staticmethod
    def _extract_variables(content: str) -> list[str]:
        """
        Extract variable names from {{variable}} placeholders in content.

        Returns a deduplicated list preserving order of first appearance.
        """
        matches = re.findall(r"\{\{(\s*\w+\s*)\}\}", content)
        seen = set()
        result = []
        for match in matches:
            name = match.strip()
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result
