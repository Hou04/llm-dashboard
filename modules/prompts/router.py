"""
Prompt Management — FastAPI router.

Exposes a complete prompt template CMS:

  POST   /v1/prompts                        — create a new prompt template
  GET    /v1/prompts                        — list prompts (paginated, searchable)
  GET    /v1/prompts/{prompt_id}            — get a prompt by ID
  PATCH  /v1/prompts/{prompt_id}            — update (creates new version)
  POST   /v1/prompts/{prompt_id}/publish    — publish a version
  POST   /v1/prompts/{name}/rollback        — rollback to a previous version
  GET    /v1/prompts/{name}/versions        — list all versions
  POST   /v1/prompts/{name}/render          — render with variable substitution
  GET    /v1/prompts/{name}/diff            — diff two versions
  GET    /v1/prompts/sdk/{name}             — SDK endpoint (get published or specific version)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.auth.dependencies import (
    get_current_user,
    require_tenant_admin,
    require_tenant_viewer,
)
from modules.auth.schemas import CurrentUser
from modules.prompts.schemas import (
    PromptCreateRequest,
    PromptUpdateRequest,
    PromptRenderRequest,
    PromptResponse,
    PromptListResponse,
    PromptVersionListResponse,
    PromptRenderResponse,
    PromptDiffResponse,
)
from modules.prompts.service import PromptService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/prompts", tags=["Prompt Management"])


# ============================================================
# DEPENDENCY
# ============================================================

async def get_prompt_service(
    session: AsyncSession = Depends(get_db),
) -> PromptService:
    return PromptService(session)


# ============================================================
# CRUD ENDPOINTS
# ============================================================

@router.post(
    "",
    response_model=PromptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new prompt template",
    description=(
        "Creates a new prompt template (version 1). "
        "If the prompt name already exists, creates a new version. "
        "Variables are auto-detected from {{variable}} syntax if not provided."
    ),
)
async def create_prompt(
    request: PromptCreateRequest,
    user: CurrentUser = Depends(require_tenant_admin),
    service: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    # Auto-scope tenant
    tenant_id = request.tenant_id
    if not user.is_super_admin():
        tenant_id = user.tenant_id
    if not tenant_id:
        raise HTTPException(400, "tenant_id is required")

    template = await service.create_prompt(request, tenant_id, user.id)
    return PromptResponse.model_validate(template)


@router.get(
    "",
    response_model=PromptListResponse,
    summary="List prompt templates",
    description=(
        "Paginated list of prompt templates with search. "
        "By default shows only published versions."
    ),
)
async def list_prompts(
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    search: Optional[str] = Query(None, description="Search in name, description, content"),
    published_only: bool = Query(True, description="Show only published versions"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: PromptService = Depends(get_prompt_service),
) -> PromptListResponse:
    effective_tenant = tenant_id
    if not user.is_super_admin():
        effective_tenant = user.tenant_id

    prompts, total = await service.list_prompts(
        tenant_id=effective_tenant,
        search=search,
        published_only=published_only,
        page=page,
        page_size=page_size,
    )
    return PromptListResponse(
        prompts=[PromptResponse.model_validate(p) for p in prompts],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{prompt_id}",
    response_model=PromptResponse,
    summary="Get a prompt template by ID",
)
async def get_prompt(
    prompt_id: str,
    user: CurrentUser = Depends(require_tenant_viewer),
    service: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    template = await service.get_by_id(prompt_id)
    if template is None:
        raise HTTPException(404, f"Prompt {prompt_id} not found")

    if not user.is_super_admin() and template.tenant_id != user.tenant_id:
        raise HTTPException(403, "Access denied to this prompt")

    return PromptResponse.model_validate(template)


@router.patch(
    "/{prompt_id}",
    response_model=PromptResponse,
    summary="Update a prompt template (creates new version)",
    description=(
        "Creates a new immutable version of the prompt. "
        "The original version is preserved. "
        "The new version starts as a draft — use /publish to activate it."
    ),
)
async def update_prompt(
    prompt_id: str,
    request: PromptUpdateRequest,
    user: CurrentUser = Depends(require_tenant_admin),
    service: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    template = await service.update_prompt(prompt_id, request, user.id)
    if template is None:
        raise HTTPException(404, f"Prompt {prompt_id} not found")
    return PromptResponse.model_validate(template)


# ============================================================
# LIFECYCLE ENDPOINTS
# ============================================================

@router.post(
    "/{prompt_id}/publish",
    response_model=PromptResponse,
    summary="Publish a prompt version to production",
    description=(
        "Makes this version the active/published one. "
        "All other versions of the same prompt are archived. "
        "This is the 'Deploy to Production' action."
    ),
)
async def publish_prompt(
    prompt_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    template = await service.publish(prompt_id, user.id)
    if template is None:
        raise HTTPException(404, f"Prompt {prompt_id} not found")
    return PromptResponse.model_validate(template)


@router.post(
    "/by-name/{name}/rollback",
    response_model=PromptResponse,
    summary="Rollback a prompt to a previous version",
    description=(
        "Re-publishes a previous version of the prompt. "
        "The current published version is archived. "
        "Specify the target version number to rollback to."
    ),
)
async def rollback_prompt(
    name: str,
    version: int = Query(..., ge=1, description="Version number to rollback to"),
    tenant_id: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_tenant_admin),
    service: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    if not effective_tenant:
        raise HTTPException(400, "tenant_id is required")

    template = await service.rollback(effective_tenant, name, version, user.id)
    if template is None:
        raise HTTPException(404, f"Prompt '{name}' version {version} not found")
    return PromptResponse.model_validate(template)


# ============================================================
# VERSION HISTORY
# ============================================================

@router.get(
    "/by-name/{name}/versions",
    response_model=PromptVersionListResponse,
    summary="List all versions of a prompt",
    description="Returns version history with diffs, newest first.",
)
async def list_versions(
    name: str,
    tenant_id: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: PromptService = Depends(get_prompt_service),
) -> PromptVersionListResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    if not effective_tenant:
        raise HTTPException(400, "tenant_id is required")

    versions = await service.list_versions(effective_tenant, name)
    if not versions:
        raise HTTPException(404, f"No versions found for prompt '{name}'")

    published_version = None
    for v in versions:
        if v.is_published:
            published_version = v.version
            break

    return PromptVersionListResponse(
        name=name,
        tenant_id=effective_tenant,
        versions=[PromptResponse.model_validate(v) for v in versions],
        total_versions=len(versions),
        published_version=published_version,
    )


# ============================================================
# RENDERING
# ============================================================

@router.post(
    "/by-name/{name}/render",
    response_model=PromptRenderResponse,
    summary="Render a prompt template with variables",
    description=(
        "Substitutes variables into the template and returns the rendered text. "
        "By default renders the published version. Specify version for a specific one."
    ),
)
async def render_prompt(
    name: str,
    request: PromptRenderRequest,
    tenant_id: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: PromptService = Depends(get_prompt_service),
) -> PromptRenderResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    if not effective_tenant:
        raise HTTPException(400, "tenant_id is required")

    try:
        result = await service.render(
            tenant_id=effective_tenant,
            name=name,
            variables=request.variables,
            version=request.version,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    if result is None:
        raise HTTPException(404, f"Prompt '{name}' not found")

    return PromptRenderResponse(**result)


# ============================================================
# DIFF
# ============================================================

@router.get(
    "/by-name/{name}/diff",
    response_model=PromptDiffResponse,
    summary="Compare two versions of a prompt",
)
async def diff_versions(
    name: str,
    version_a: int = Query(..., ge=1, description="First version"),
    version_b: int = Query(..., ge=1, description="Second version"),
    tenant_id: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: PromptService = Depends(get_prompt_service),
) -> PromptDiffResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    if not effective_tenant:
        raise HTTPException(400, "tenant_id is required")

    result = await service.diff_versions(effective_tenant, name, version_a, version_b)
    if result is None:
        raise HTTPException(404, f"One or both versions not found for prompt '{name}'")

    return PromptDiffResponse(**result)


# ============================================================
# SDK ENDPOINT
# ============================================================

@router.get(
    "/sdk/{name}",
    response_model=PromptResponse,
    summary="SDK endpoint — get a prompt by name",
    description=(
        "Returns the published version by default, or a specific version. "
        "This is the primary endpoint for SDK integration:\n"
        "  `client.prompts.get('summarize_report', version='latest')`"
    ),
)
async def sdk_get_prompt(
    name: str,
    tenant_id: str = Query(..., description="Tenant ID"),
    version: Optional[int] = Query(None, description="Specific version (default = published)"),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: PromptService = Depends(get_prompt_service),
) -> PromptResponse:
    if not user.is_super_admin() and tenant_id != user.tenant_id:
        raise HTTPException(403, "Access denied to this tenant's prompts")

    template = await service.get_prompt(tenant_id, name, version)
    if template is None:
        raise HTTPException(404, f"Prompt '{name}' not found")

    return PromptResponse.model_validate(template)
