"""
FastAPI routes for managing Cosmos DB prompt templates.

Endpoints:

    GET  /api/v1/prompt-templates
    GET  /api/v1/prompt-templates/{rule_id}/{prompt_id}
    POST /api/v1/prompt-templates
    POST /api/v1/prompt-templates/{rule_id}/{prompt_id}/disable
    POST /api/v1/prompt-templates/{rule_id}/{prompt_id}/enable

The POST /api/v1/prompt-templates endpoint uses Cosmos DB upsert:

    - If the document does not exist, it is created.
    - If the document already exists, it is updated.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    status,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.agents.precall_agent.cosmos_prompt_store import (
    InvalidPromptDocumentError,
    PromptDisabledError,
    PromptNotFoundError,
    PromptStoreError,
    get_prompt_document,
    list_prompt_templates,
    upsert_prompt_template,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/prompt-templates",
    tags=["Prompt Templates"],
)


# ---------------------------------------------------------------------------
# REQUEST MODELS
# ---------------------------------------------------------------------------

class PromptTemplateUpsertRequest(BaseModel):
    """
    Request body used to create or edit a prompt template.

    Since Cosmos DB upsert is used:

        New prompt_id + rule_id:
            Creates a document.

        Existing prompt_id + rule_id:
            Updates the document.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    prompt_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description=(
            "Cosmos DB document ID. "
            "Auto-generated as {rule_id}-v{version} if omitted."
        ),
        examples=[
            "collections-followup-v1"
        ],
    )

    rule_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "The actual /rule_id partition-key value."
        ),
        examples=[
            "collections-followup"
        ],
    )

    template: str = Field(
        ...,
        min_length=1,
        description=(
            "Complete prompt-template text."
        ),
    )

    version: int = Field(
        default=1,
        ge=1,
        description="Prompt-template version.",
    )

    enabled: bool = Field(
        default=True,
        description=(
            "Whether this prompt can be used."
        ),
    )

    description: Optional[str] = Field(
        default=None,
        max_length=1000,
    )

    status: str = Field(
        default="draft",
        max_length=50,
        description=(
            "Template status: draft, pending, approved, rejected."
        ),
    )

    edited_by: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    approved_by: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    updated_by: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
    )

    @field_validator("prompt_id")
    @classmethod
    def validate_prompt_id(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalized_value = value.strip()

        if not normalized_value:
            return None

        invalid_characters = {
            "/",
            "\\",
            "?",
            "#",
        }

        discovered_characters = (
            invalid_characters.intersection(
                normalized_value
            )
        )

        if discovered_characters:
            raise ValueError(
                "prompt_id cannot contain these characters: "
                "/, \\, ?, #"
            )

        return normalized_value

    @field_validator("rule_id")
    @classmethod
    def validate_rule_id(
        cls,
        value: str,
    ) -> str:
        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                "rule_id cannot be empty."
            )

        if normalized_value == "/rule_id":
            raise ValueError(
                "Provide the actual partition-key value, "
                "not the path '/rule_id'."
            )

        return normalized_value

    @field_validator("template")
    @classmethod
    def validate_template(
        cls,
        value: str,
    ) -> str:
        if not value.strip():
            raise ValueError(
                "template cannot be empty."
            )

        return value


class PromptStatusRequest(BaseModel):
    """Request used to enable or disable a template."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    updated_by: Optional[str] = Field(
        default=None,
        max_length=255,
    )


# ---------------------------------------------------------------------------
# RESPONSE MODELS
# ---------------------------------------------------------------------------

class PromptTemplateResponse(BaseModel):
    """Prompt-template response."""

    model_config = ConfigDict(
        extra="allow",
    )

    id: str
    rule_id: str
    template: str
    version: int
    enabled: bool
    status: str = "draft"
    description: str = ""
    edited_by: str = ""
    approved_by: str = ""
    updated_by: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    document_type: str = "prompt_template"
    metadata: Dict[str, Any] = Field(
        default_factory=dict
    )


class PromptTemplateSummaryResponse(BaseModel):
    """
    Response that includes template body, status, and approval info.
    """

    model_config = ConfigDict(
        extra="allow",
    )

    id: str
    rule_id: str
    version: int
    enabled: bool
    status: str = "draft"
    is_current_prompt: bool = False
    template: str = ""
    description: str = ""
    edited_by: str = ""
    approved_by: str = ""
    updated_by: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    document_type: str = "prompt_template"
    metadata: Dict[str, Any] = Field(
        default_factory=dict
    )


class PromptDraftWithCurrentResponse(BaseModel):
    """A draft template paired with the current approved prompt for comparison."""

    model_config = ConfigDict(
        extra="allow",
    )

    draft: PromptTemplateSummaryResponse
    current_approved: Optional[
        PromptTemplateSummaryResponse
    ] = None


class PromptDraftListResponse(BaseModel):
    """Response returned when listing drafts alongside current prompts."""

    count: int
    templates: List[
        PromptDraftWithCurrentResponse
    ]


class PromptTemplateListResponse(BaseModel):
    """Response returned when listing prompt templates."""

    count: int
    templates: List[
        PromptTemplateSummaryResponse
    ]


class PromptTemplateWriteResponse(BaseModel):
    """Response returned after creating or editing a prompt."""

    message: str
    prompt: PromptTemplateResponse


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _remove_cosmos_system_fields(
    document: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Remove Cosmos DB internal properties before returning
    the document through the API.
    """

    system_fields = {
        "_rid",
        "_self",
        "_etag",
        "_attachments",
        "_ts",
    }

    return {
        key: value
        for key, value in document.items()
        if key not in system_fields
    }


def _build_summary(
    document: Dict[str, Any],
    is_current_prompt: bool = False,
) -> Dict[str, Any]:
    """
    Build a response including the full template, status, and approval info.
    """

    cleaned_document = (
        _remove_cosmos_system_fields(
            document
        )
    )

    return {
        "id": cleaned_document.get(
            "id",
            "",
        ),
        "rule_id": cleaned_document.get(
            "rule_id",
            "",
        ),
        "version": cleaned_document.get(
            "version",
            1,
        ),
        "enabled": cleaned_document.get(
            "enabled",
            True,
        ),
        "status": cleaned_document.get(
            "status",
            "draft",
        ),
        "is_current_prompt": is_current_prompt,
        "template": cleaned_document.get(
            "template",
            "",
        ),
        "description": cleaned_document.get(
            "description",
            "",
        ),
        "edited_by": cleaned_document.get(
            "edited_by",
            "",
        ),
        "approved_by": cleaned_document.get(
            "approved_by",
            "",
        ),
        "updated_by": cleaned_document.get(
            "updated_by",
            "",
        ),
        "created_at": cleaned_document.get(
            "created_at"
        ),
        "updated_at": cleaned_document.get(
            "updated_at"
        ),
        "document_type": cleaned_document.get(
            "document_type",
            "prompt_template",
        ),
        "metadata": cleaned_document.get(
            "metadata",
            {},
        ),
    }


def _handle_prompt_store_error(
    exception: Exception,
) -> None:
    """
    Convert prompt-store exceptions to HTTP responses.
    """

    if isinstance(
        exception,
        PromptNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exception),
        ) from exception

    if isinstance(
        exception,
        PromptDisabledError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exception),
        ) from exception

    if isinstance(
        exception,
        InvalidPromptDocumentError,
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exception),
        ) from exception

    if isinstance(
        exception,
        PromptStoreError,
    ):
        logger.exception(
            "Cosmos DB prompt-store operation failed."
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The prompt-template store is currently "
                "unavailable."
            ),
        ) from exception

    logger.exception(
        "Unexpected prompt-template API error."
    )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            "An unexpected error occurred while processing "
            "the prompt template."
        ),
    ) from exception


# ---------------------------------------------------------------------------
# GET: LIST PROMPT TEMPLATES
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=PromptTemplateListResponse,
    status_code=status.HTTP_200_OK,
    summary="List prompt templates",
)
def get_prompt_templates(
    rule_id: Optional[str] = Query(
        default=None,
        description=(
            "Optional rule_id partition value."
        ),
        examples=[
            "collections-followup"
        ],
    ),
    enabled_only: bool = Query(
        default=False,
        description=(
            "Return only enabled prompt templates."
        ),
    ),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description=(
            "Filter by status: draft, pending, approved, rejected."
        ),
    ),
) -> PromptTemplateListResponse:
    """
    Retrieve prompt-template summaries.

    Examples:

        GET /api/v1/prompt-templates

        GET /api/v1/prompt-templates?rule_id=collections-followup

        GET /api/v1/prompt-templates?enabled_only=true

        GET /api/v1/prompt-templates?status=draft
    """

    try:
        documents = list_prompt_templates(
            rule_id=rule_id,
            enabled_only=enabled_only,
            status_filter=status_filter,
        )

        # Identify the latest approved prompt per rule_id.
        latest_approved: Dict[str, str] = {}
        for doc in documents:
            doc_rule = doc.get("rule_id", "")
            if doc.get("status") == "approved" and doc.get("enabled") is True:
                if doc_rule not in latest_approved:
                    latest_approved[doc_rule] = doc.get("id", "")

        summaries = [
            PromptTemplateSummaryResponse(
                **_build_summary(
                    document,
                    is_current_prompt=(
                        document.get("id", "") == latest_approved.get(
                            document.get("rule_id", ""), ""
                        )
                    ),
                )
            )
            for document in documents
        ]

        return PromptTemplateListResponse(
            count=len(summaries),
            templates=summaries,
        )

    except Exception as exc:
        _handle_prompt_store_error(exc)
        raise


# ---------------------------------------------------------------------------
# GET: LIST DRAFT PROMPT TEMPLATES
# ---------------------------------------------------------------------------

@router.get(
    "/drafts",
    response_model=PromptDraftListResponse,
    status_code=status.HTTP_200_OK,
    summary="List draft prompt templates with current approved",
)
def get_draft_prompt_templates(
    rule_id: Optional[str] = Query(
        default=None,
        description=(
            "Optional rule_id partition value."
        ),
        examples=[
            "collections-followup"
        ],
    ),
) -> PromptDraftListResponse:
    """
    Retrieve draft templates paired with the current approved prompt
    for the same rule_id, making it easy to compare changes.

    Examples:

        GET /api/v1/prompt-templates/drafts

        GET /api/v1/prompt-templates/drafts?rule_id=collections-followup
    """

    try:
        draft_documents = list_prompt_templates(
            rule_id=rule_id,
            status_filter="draft",
        )

        # Collect unique rule_ids from drafts.
        draft_rule_ids = {
            doc.get("rule_id", "")
            for doc in draft_documents
            if doc.get("rule_id")
        }

        # Fetch approved prompts for those rule_ids.
        approved_by_rule: Dict[
            str, Dict[str, Any]
        ] = {}
        for rid in draft_rule_ids:
            approved_docs = list_prompt_templates(
                rule_id=rid,
                status_filter="approved",
                enabled_only=True,
            )
            if approved_docs:
                approved_by_rule[rid] = approved_docs[0]

        results = []
        for doc in draft_documents:
            doc_rule = doc.get("rule_id", "")
            draft_summary = PromptTemplateSummaryResponse(
                **_build_summary(doc, is_current_prompt=False)
            )

            current_approved = None
            if doc_rule in approved_by_rule:
                current_approved = PromptTemplateSummaryResponse(
                    **_build_summary(
                        approved_by_rule[doc_rule],
                        is_current_prompt=True,
                    )
                )

            results.append(
                PromptDraftWithCurrentResponse(
                    draft=draft_summary,
                    current_approved=current_approved,
                )
            )

        return PromptDraftListResponse(
            count=len(results),
            templates=results,
        )

    except Exception as exc:
        _handle_prompt_store_error(exc)
        raise


# ---------------------------------------------------------------------------
# GET: RETRIEVE ONE PROMPT TEMPLATE
# ---------------------------------------------------------------------------

@router.get(
    "/{rule_id}/{prompt_id}",
    response_model=PromptTemplateResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve one prompt template",
)
def get_one_prompt_template(
    rule_id: str,
    prompt_id: str,
    include_disabled: bool = Query(
        default=False,
        description=(
            "Allow retrieval of a disabled template."
        ),
    ),
) -> PromptTemplateResponse:
    """
    Retrieve a complete prompt template.

    Example:

        GET /api/v1/prompt-templates/collections-followup/collections-followup-v1
    """

    try:
        document = get_prompt_document(
            prompt_id=prompt_id,
            rule_id=rule_id,
            use_cache=True,
            require_enabled=not include_disabled,
        )

        cleaned_document = (
            _remove_cosmos_system_fields(
                document
            )
        )

        return PromptTemplateResponse(
            **cleaned_document
        )

    except Exception as exc:
        _handle_prompt_store_error(exc)
        raise


# ---------------------------------------------------------------------------
# POST: CREATE OR EDIT A PROMPT TEMPLATE
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=PromptTemplateWriteResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or edit a prompt template",
)
def create_or_edit_prompt_template(
    request: PromptTemplateUpsertRequest,
) -> PromptTemplateWriteResponse:
    """
    Create or edit a prompt template using Cosmos DB upsert.

    If the combination of prompt_id and rule_id already exists,
    the document is updated.

    If it does not exist, a new document is created.
    """

    try:
        # Auto-generate prompt_id from rule_id + version if not provided.
        prompt_id = request.prompt_id or f"{request.rule_id}-v{request.version}"

        document_already_exists = True

        try:
            get_prompt_document(
                prompt_id=prompt_id,
                rule_id=request.rule_id,
                use_cache=False,
                require_enabled=False,
            )
        except PromptNotFoundError:
            document_already_exists = False

        saved_document = upsert_prompt_template(
            prompt_id=prompt_id,
            rule_id=request.rule_id,
            template=request.template,
            version=request.version,
            enabled=request.enabled,
            status=request.status,
            description=request.description,
            updated_by=(
                request.updated_by
                or "prompt-template-api"
            ),
            edited_by=request.edited_by,
            approved_by=request.approved_by,
            metadata=request.metadata,
        )

        cleaned_document = (
            _remove_cosmos_system_fields(
                saved_document
            )
        )

        message = (
            "Prompt template updated successfully."
            if document_already_exists
            else "Prompt template created successfully."
        )

        return PromptTemplateWriteResponse(
            message=message,
            prompt=PromptTemplateResponse(
                **cleaned_document
            ),
        )

    except Exception as exc:
        _handle_prompt_store_error(exc)
        raise


# ---------------------------------------------------------------------------
# POST: DISABLE A PROMPT TEMPLATE
# ---------------------------------------------------------------------------

@router.post(
    "/{rule_id}/{prompt_id}/disable",
    response_model=PromptTemplateWriteResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable a prompt template",
)
def disable_prompt_template(
    rule_id: str,
    prompt_id: str,
    request: PromptStatusRequest,
) -> PromptTemplateWriteResponse:
    """
    Disable an existing prompt while preserving its template.
    """

    try:
        existing_document = get_prompt_document(
            prompt_id=prompt_id,
            rule_id=rule_id,
            use_cache=False,
            require_enabled=False,
        )

        saved_document = upsert_prompt_template(
            prompt_id=prompt_id,
            rule_id=rule_id,
            template=existing_document["template"],
            version=int(
                existing_document.get(
                    "version",
                    1,
                )
            ),
            enabled=False,
            status=existing_document.get(
                "status",
                "draft",
            ),
            description=existing_document.get(
                "description",
                "",
            ),
            updated_by=(
                request.updated_by
                or "prompt-template-api"
            ),
            edited_by=existing_document.get(
                "edited_by",
                "",
            ),
            approved_by=existing_document.get(
                "approved_by",
                "",
            ),
            metadata=existing_document.get(
                "metadata",
                {},
            ),
        )

        cleaned_document = (
            _remove_cosmos_system_fields(
                saved_document
            )
        )

        return PromptTemplateWriteResponse(
            message=(
                "Prompt template disabled successfully."
            ),
            prompt=PromptTemplateResponse(
                **cleaned_document
            ),
        )

    except Exception as exc:
        _handle_prompt_store_error(exc)
        raise


# ---------------------------------------------------------------------------
# POST: ENABLE A PROMPT TEMPLATE
# ---------------------------------------------------------------------------

@router.post(
    "/{rule_id}/{prompt_id}/enable",
    response_model=PromptTemplateWriteResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable a prompt template",
)
def enable_prompt_template(
    rule_id: str,
    prompt_id: str,
    request: PromptStatusRequest,
) -> PromptTemplateWriteResponse:
    """
    Enable an existing prompt while preserving its template.
    """

    try:
        existing_document = get_prompt_document(
            prompt_id=prompt_id,
            rule_id=rule_id,
            use_cache=False,
            require_enabled=False,
        )

        saved_document = upsert_prompt_template(
            prompt_id=prompt_id,
            rule_id=rule_id,
            template=existing_document["template"],
            version=int(
                existing_document.get(
                    "version",
                    1,
                )
            ),
            enabled=True,
            status=existing_document.get(
                "status",
                "draft",
            ),
            description=existing_document.get(
                "description",
                "",
            ),
            updated_by=(
                request.updated_by
                or "prompt-template-api"
            ),
            edited_by=existing_document.get(
                "edited_by",
                "",
            ),
            approved_by=existing_document.get(
                "approved_by",
                "",
            ),
            metadata=existing_document.get(
                "metadata",
                {},
            ),
        )

        cleaned_document = (
            _remove_cosmos_system_fields(
                saved_document
            )
        )

        return PromptTemplateWriteResponse(
            message=(
                "Prompt template enabled successfully."
            ),
            prompt=PromptTemplateResponse(
                **cleaned_document
            ),
        )

    except Exception as exc:
        _handle_prompt_store_error(exc)
        raise