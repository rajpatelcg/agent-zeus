"""
Cosmos DB prompt-template storage.

Database:
    bussiness_rules

Container:
    rules_v1

Partition-key path:
    /rule_id

Required configuration:
    COSMOS_ENDPOINT
    COSMOS_KEY

The following functions are available:

    test_cosmos_connection()
    get_prompt_document()
    get_prompt_template()
    find_latest_prompt_by_rule_id()
    upsert_prompt_template()
    upsert_prompt_template_from_file()
    delete_prompt_template()
    clear_prompt_cache()
"""

import datetime
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from app.config.src import COSMOS_ENDPOINT, COSMOS_KEY


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# COSMOS DB CONFIGURATION
# ---------------------------------------------------------------------------

# Important:
# Do not add a comma after these string assignments.
# A trailing comma would create a tuple instead of a string.

COSMOS_DATABASE_NAME = "bussiness_rules"
COSMOS_PROMPT_CONTAINER_NAME = "rules_v1"

# The container partition-key path is /rule_id.
# When reading or writing a document, pass the actual rule_id value,
# not the literal string "/rule_id".

COSMOS_PARTITION_KEY_FIELD = "rule_id"

PROMPT_CACHE_TTL_SECONDS = int(
    os.getenv(
        "PROMPT_CACHE_TTL_SECONDS",
        "300",
    )
)


# ---------------------------------------------------------------------------
# CUSTOM EXCEPTIONS
# ---------------------------------------------------------------------------

class PromptStoreError(RuntimeError):
    """Base exception for prompt-store operations."""


class PromptNotFoundError(PromptStoreError):
    """Raised when a requested prompt cannot be found."""


class PromptDisabledError(PromptStoreError):
    """Raised when a requested prompt is disabled."""


class InvalidPromptDocumentError(PromptStoreError):
    """Raised when a stored prompt document is invalid."""


class PromptConfigurationError(PromptStoreError):
    """Raised when Cosmos DB configuration is invalid."""


# ---------------------------------------------------------------------------
# CONFIGURATION VALIDATION
# ---------------------------------------------------------------------------

def _validate_string_configuration(
    name: str,
    value: Any,
    secret: bool = False,
) -> str:
    """
    Validate and normalize a Cosmos DB configuration string.

    Args:
        name:
            Configuration-variable name.

        value:
            Configuration value.

        secret:
            If True, the value will not be included in an error message.

    Returns:
        The stripped string.
    """

    if not isinstance(value, str):
        displayed_value = (
            "[hidden]"
            if secret
            else repr(value)
        )

        raise PromptConfigurationError(
            f"{name} must be a string, but received "
            f"{type(value).__name__}: {displayed_value}. "
            "Remove any trailing comma from its assignment."
        )

    normalized_value = value.strip()

    if not normalized_value:
        raise PromptConfigurationError(
            f"{name} cannot be empty."
        )

    return normalized_value


def validate_cosmos_configuration() -> None:
    """
    Validate all Cosmos DB configuration values.

    This catches common problems such as:

        COSMOS_DATABASE_NAME = "bussiness_rules",

    The trailing comma above creates a tuple instead of a string.
    """

    _validate_string_configuration(
        name="COSMOS_ENDPOINT",
        value=COSMOS_ENDPOINT,
    )

    _validate_string_configuration(
        name="COSMOS_KEY",
        value=COSMOS_KEY,
        secret=True,
    )

    _validate_string_configuration(
        name="COSMOS_DATABASE_NAME",
        value=COSMOS_DATABASE_NAME,
    )

    _validate_string_configuration(
        name="COSMOS_PROMPT_CONTAINER_NAME",
        value=COSMOS_PROMPT_CONTAINER_NAME,
    )

    if not isinstance(PROMPT_CACHE_TTL_SECONDS, int):
        raise PromptConfigurationError(
            "PROMPT_CACHE_TTL_SECONDS must be an integer."
        )

    if PROMPT_CACHE_TTL_SECONDS < 0:
        raise PromptConfigurationError(
            "PROMPT_CACHE_TTL_SECONDS cannot be negative."
        )


validate_cosmos_configuration()


# ---------------------------------------------------------------------------
# COSMOS DB CLIENTS
# ---------------------------------------------------------------------------

# Use only the synchronous CosmosClient:
#
#     from azure.cosmos import CosmosClient
#
# Do not also import:
#
#     from azure.cosmos.aio import CosmosClient
#
# because that would replace the synchronous client name with the async one.

_cosmos_client = CosmosClient(
    url=COSMOS_ENDPOINT.strip(),
    credential=COSMOS_KEY.strip(),
)

_database_client = _cosmos_client.get_database_client(
    COSMOS_DATABASE_NAME
)

_prompt_container = _database_client.get_container_client(
    COSMOS_PROMPT_CONTAINER_NAME
)


# ---------------------------------------------------------------------------
# IN-MEMORY CACHE
# ---------------------------------------------------------------------------

# Cache key:
#     (prompt_id, rule_id)
#
# Cache value:
#     {
#         "cached_at": monotonic timestamp,
#         "document": Cosmos document
#     }

_prompt_cache: Dict[
    Tuple[str, str],
    Dict[str, Any],
] = {}

_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# VALIDATION HELPERS
# ---------------------------------------------------------------------------

def _validate_prompt_id(prompt_id: Any) -> str:
    """Validate and normalize a Cosmos document ID."""

    if not isinstance(prompt_id, str):
        raise TypeError(
            "prompt_id must be a string."
        )

    normalized_prompt_id = prompt_id.strip()

    if not normalized_prompt_id:
        raise ValueError(
            "prompt_id cannot be empty."
        )

    return normalized_prompt_id


def _validate_rule_id(rule_id: Any) -> str:
    """
    Validate and normalize the rule_id partition-key value.

    This must be the actual partition-key value, such as:

        collections-followup

    It must not be the path:

        /rule_id
    """

    if not isinstance(rule_id, str):
        raise TypeError(
            "rule_id must be a string."
        )

    normalized_rule_id = rule_id.strip()

    if not normalized_rule_id:
        raise ValueError(
            "rule_id cannot be empty."
        )

    if normalized_rule_id == "/rule_id":
        raise ValueError(
            "Pass the actual rule_id value, not the partition-key "
            "path '/rule_id'. Example: 'collections-followup'."
        )

    return normalized_rule_id


def _validate_template(template: Any) -> str:
    """Validate and normalize prompt-template text."""

    if not isinstance(template, str):
        raise TypeError(
            "template must be a string."
        )

    normalized_template = template.strip()

    if not normalized_template:
        raise ValueError(
            "template cannot be empty."
        )

    return normalized_template


def _validate_version(version: Any) -> int:
    """Validate a prompt version."""

    if isinstance(version, bool) or not isinstance(version, int):
        raise TypeError(
            "version must be an integer."
        )

    if version < 1:
        raise ValueError(
            "version must be greater than or equal to 1."
        )

    return version


def _validate_prompt_document(
    document: Any,
    expected_prompt_id: Optional[str] = None,
    expected_rule_id: Optional[str] = None,
    require_enabled: bool = True,
) -> Dict[str, Any]:
    """
    Validate a prompt document returned from Cosmos DB.

    Returns:
        The validated document.
    """

    if not isinstance(document, dict):
        raise InvalidPromptDocumentError(
            "The stored prompt is not a valid JSON document."
        )

    document_id = document.get("id")

    if not isinstance(document_id, str) or not document_id.strip():
        raise InvalidPromptDocumentError(
            "The stored prompt does not contain a valid 'id'."
        )

    document_rule_id = document.get(
        COSMOS_PARTITION_KEY_FIELD
    )

    if (
        not isinstance(document_rule_id, str)
        or not document_rule_id.strip()
    ):
        raise InvalidPromptDocumentError(
            "The stored prompt does not contain a valid 'rule_id'."
        )

    if (
        expected_prompt_id is not None
        and document_id != expected_prompt_id
    ):
        raise InvalidPromptDocumentError(
            f"Unexpected prompt document ID. "
            f"Expected '{expected_prompt_id}', "
            f"but found '{document_id}'."
        )

    if (
        expected_rule_id is not None
        and document_rule_id != expected_rule_id
    ):
        raise InvalidPromptDocumentError(
            f"Unexpected rule_id. "
            f"Expected '{expected_rule_id}', "
            f"but found '{document_rule_id}'."
        )

    if require_enabled and document.get("enabled") is False:
        raise PromptDisabledError(
            f"Prompt '{document_id}' is disabled."
        )

    template = document.get("template")

    if not isinstance(template, str):
        raise InvalidPromptDocumentError(
            f"Prompt '{document_id}' does not contain "
            "a valid 'template' string."
        )

    if not template.strip():
        raise InvalidPromptDocumentError(
            f"Prompt '{document_id}' contains an empty template."
        )

    return document


def _utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""

    return datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()


# ---------------------------------------------------------------------------
# CACHE OPERATIONS
# ---------------------------------------------------------------------------

def clear_prompt_cache(
    prompt_id: Optional[str] = None,
    rule_id: Optional[str] = None,
) -> None:
    """
    Clear one cached prompt or the complete cache.

    Clear the entire cache:

        clear_prompt_cache()

    Clear one prompt:

        clear_prompt_cache(
            prompt_id="collections-followup-v1",
            rule_id="collections-followup",
        )
    """

    with _cache_lock:
        if prompt_id is None and rule_id is None:
            _prompt_cache.clear()
            return

        if prompt_id is None or rule_id is None:
            raise ValueError(
                "Both prompt_id and rule_id must be provided "
                "when clearing a specific cached prompt."
            )

        normalized_prompt_id = _validate_prompt_id(
            prompt_id
        )

        normalized_rule_id = _validate_rule_id(
            rule_id
        )

        _prompt_cache.pop(
            (
                normalized_prompt_id,
                normalized_rule_id,
            ),
            None,
        )


def _get_cached_prompt(
    prompt_id: str,
    rule_id: str,
) -> Optional[Dict[str, Any]]:
    """Return a valid cached prompt, if one is available."""

    if PROMPT_CACHE_TTL_SECONDS == 0:
        return None

    cache_key = (
        prompt_id,
        rule_id,
    )

    current_time = time.monotonic()

    with _cache_lock:
        cached_entry = _prompt_cache.get(
            cache_key
        )

        if cached_entry is None:
            return None

        cached_at = cached_entry.get(
            "cached_at",
            0,
        )

        cache_age = (
            current_time - cached_at
        )

        if cache_age >= PROMPT_CACHE_TTL_SECONDS:
            _prompt_cache.pop(
                cache_key,
                None,
            )
            return None

        cached_document = cached_entry.get(
            "document"
        )

        if not isinstance(cached_document, dict):
            _prompt_cache.pop(
                cache_key,
                None,
            )
            return None

        return cached_document


def _set_cached_prompt(
    prompt_id: str,
    rule_id: str,
    document: Dict[str, Any],
) -> None:
    """Save a prompt document in the local cache."""

    if PROMPT_CACHE_TTL_SECONDS == 0:
        return

    cache_key = (
        prompt_id,
        rule_id,
    )

    with _cache_lock:
        _prompt_cache[cache_key] = {
            "cached_at": time.monotonic(),
            "document": document,
        }


# ---------------------------------------------------------------------------
# CONNECTION TEST
# ---------------------------------------------------------------------------

def test_cosmos_connection() -> Dict[str, Any]:
    """
    Test access to the configured database and container.

    Returns:
        Database, container, and partition-key information.

    This function does not display or return the Cosmos DB key.
    """

    try:
        database_properties = _database_client.read()
        container_properties = _prompt_container.read()

        partition_key_paths = (
            container_properties.get(
                "partitionKey",
                {},
            ).get(
                "paths",
                [],
            )
        )

        return {
            "status": "success",
            "database": database_properties.get(
                "id",
                COSMOS_DATABASE_NAME,
            ),
            "container": container_properties.get(
                "id",
                COSMOS_PROMPT_CONTAINER_NAME,
            ),
            "partition_key_paths": partition_key_paths,
            "expected_partition_key": "/rule_id",
        }

    except CosmosResourceNotFoundError as exc:
        raise PromptStoreError(
            "The configured Cosmos DB database or container "
            "could not be found. "
            f"Database: '{COSMOS_DATABASE_NAME}', "
            f"container: '{COSMOS_PROMPT_CONTAINER_NAME}'."
        ) from exc

    except CosmosHttpResponseError as exc:
        raise PromptStoreError(
            "Unable to access the configured Cosmos DB "
            f"database or container. Status code: "
            f"{exc.status_code}."
        ) from exc


# ---------------------------------------------------------------------------
# READ OPERATIONS
# ---------------------------------------------------------------------------

def get_prompt_document(
    prompt_id: str,
    rule_id: str,
    use_cache: bool = True,
    require_enabled: bool = True,
) -> Dict[str, Any]:
    """
    Retrieve a prompt document using its ID and rule_id.

    Container partition-key path:
        /rule_id

    Example:

        document = get_prompt_document(
            prompt_id="collections-followup-v1",
            rule_id="collections-followup",
        )

    Note:
        The partition_key parameter receives the actual rule_id value,
        not the string "/rule_id".
    """

    normalized_prompt_id = _validate_prompt_id(
        prompt_id
    )

    normalized_rule_id = _validate_rule_id(
        rule_id
    )

    if use_cache:
        cached_document = _get_cached_prompt(
            prompt_id=normalized_prompt_id,
            rule_id=normalized_rule_id,
        )

        if cached_document is not None:
            return _validate_prompt_document(
                document=cached_document,
                expected_prompt_id=normalized_prompt_id,
                expected_rule_id=normalized_rule_id,
                require_enabled=require_enabled,
            )

    try:
        document = _prompt_container.read_item(
            item=normalized_prompt_id,
            partition_key=normalized_rule_id,
        )

    except CosmosResourceNotFoundError as exc:
        raise PromptNotFoundError(
            f"Prompt document '{normalized_prompt_id}' "
            f"was not found under rule_id partition "
            f"'{normalized_rule_id}'."
        ) from exc

    except CosmosHttpResponseError as exc:
        raise PromptStoreError(
            f"Could not retrieve prompt "
            f"'{normalized_prompt_id}' from Cosmos DB. "
            f"Status code: {exc.status_code}."
        ) from exc

    validated_document = _validate_prompt_document(
        document=document,
        expected_prompt_id=normalized_prompt_id,
        expected_rule_id=normalized_rule_id,
        require_enabled=require_enabled,
    )

    if use_cache:
        _set_cached_prompt(
            prompt_id=normalized_prompt_id,
            rule_id=normalized_rule_id,
            document=validated_document,
        )

    return validated_document


def get_prompt_template(
    prompt_id: str,
    rule_id: str,
    use_cache: bool = True,
) -> str:
    """
    Retrieve only the enabled prompt-template text.

    Example:

        template = get_prompt_template(
            prompt_id="collections-followup-v1",
            rule_id="collections-followup",
        )
    """

    document = get_prompt_document(
        prompt_id=prompt_id,
        rule_id=rule_id,
        use_cache=use_cache,
        require_enabled=True,
    )

    return document["template"]


def find_latest_prompt_by_rule_id(
    rule_id: str,
    enabled_only: bool = True,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Find the highest-version prompt in a rule_id partition.

    Use this function if you know the rule_id but do not know
    the Cosmos document ID.

    Example:

        document = find_latest_prompt_by_rule_id(
            rule_id="collections-followup",
        )

        template = document["template"]
    """

    normalized_rule_id = _validate_rule_id(
        rule_id
    )

    query = """
    SELECT *
    FROM c
    WHERE c.rule_id = @rule_id
      AND c.document_type = @document_type
    """

    parameters = [
        {
            "name": "@rule_id",
            "value": normalized_rule_id,
        },
        {
            "name": "@document_type",
            "value": "prompt_template",
        },
    ]

    if enabled_only:
        query += " AND c.enabled = true"

    try:
        documents = list(
            _prompt_container.query_items(
                query=query,
                parameters=parameters,
                partition_key=normalized_rule_id,
            )
        )

    except CosmosHttpResponseError as exc:
        raise PromptStoreError(
            f"Could not query prompts for rule_id "
            f"'{normalized_rule_id}'. "
            f"Status code: {exc.status_code}."
        ) from exc

    if not documents:
        enabled_text = (
            "enabled "
            if enabled_only
            else ""
        )

        raise PromptNotFoundError(
            f"No {enabled_text}prompt was found for "
            f"rule_id '{normalized_rule_id}'."
        )

    def version_sort_value(
        document: Dict[str, Any],
    ) -> int:
        version = document.get("version", 0)

        if isinstance(version, bool):
            return 0

        if isinstance(version, int):
            return version

        try:
            return int(version)
        except (TypeError, ValueError):
            return 0

    documents.sort(
        key=version_sort_value,
        reverse=True,
    )

    selected_document = _validate_prompt_document(
        document=documents[0],
        expected_rule_id=normalized_rule_id,
        require_enabled=enabled_only,
    )

    selected_prompt_id = selected_document["id"]

    if use_cache:
        _set_cached_prompt(
            prompt_id=selected_prompt_id,
            rule_id=normalized_rule_id,
            document=selected_document,
        )

    return selected_document


def get_latest_prompt_template(
    rule_id: str,
    use_cache: bool = True,
) -> str:
    """
    Retrieve the highest-version enabled template for a rule_id.
    """

    document = find_latest_prompt_by_rule_id(
        rule_id=rule_id,
        enabled_only=True,
        use_cache=use_cache,
    )

    return document["template"]

def list_prompt_templates(
    rule_id: Optional[str] = None,
    enabled_only: bool = False,
    status_filter: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """
    Retrieve prompt templates from Cosmos DB.

    Args:
        rule_id:
            Optional rule_id partition-key value.

            If supplied, only prompt templates within that
            rule_id partition are returned.

            Example:
                collections-followup

        enabled_only:
            If True, return only enabled prompt templates.

        status_filter:
            If supplied, return only templates with this status
            (e.g. "draft", "approved", "pending", "rejected").

    Returns:
        A list of prompt-template documents.
    """

    query = """
    SELECT *
    FROM c
    WHERE c.document_type = @document_type
    """

    parameters = [
        {
            "name": "@document_type",
            "value": "prompt_template",
        }
    ]

    normalized_rule_id: Optional[str] = None

    if rule_id is not None:
        normalized_rule_id = _validate_rule_id(
            rule_id
        )

        query += """
        AND c.rule_id = @rule_id
        """

        parameters.append(
            {
                "name": "@rule_id",
                "value": normalized_rule_id,
            }
        )

    if enabled_only:
        query += """
        AND c.enabled = true
        """

    if status_filter is not None:
        query += """
        AND c.status = @status
        """

        parameters.append(
            {
                "name": "@status",
                "value": status_filter.strip(),
            }
        )

    try:
        if normalized_rule_id is not None:
            # Query only one partition.
            documents = list(
                _prompt_container.query_items(
                    query=query,
                    parameters=parameters,
                    partition_key=normalized_rule_id,
                )
            )
        else:
            # Query all rule_id partitions.
            documents = list(
                _prompt_container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )

    except CosmosHttpResponseError as exc:
        raise PromptStoreError(
            "Could not retrieve prompt templates from "
            f"Cosmos DB. Status code: {exc.status_code}."
        ) from exc

    def get_version(
        document: Dict[str, Any],
    ) -> int:
        """Convert the stored version into a sortable integer."""

        version = document.get(
            "version",
            0,
        )

        if isinstance(version, bool):
            return 0

        if isinstance(version, int):
            return version

        try:
            return int(version)
        except (TypeError, ValueError):
            return 0

    # Sort first by rule_id and then by newest version.
    documents.sort(
        key=lambda document: (
            str(
                document.get(
                    "rule_id",
                    "",
                )
            ),
            -get_version(document),
        )
    )

    return documents
# ---------------------------------------------------------------------------
# UPSERT OPERATIONS
# ---------------------------------------------------------------------------

def upsert_prompt_template(
    prompt_id: str,
    rule_id: str,
    template: str,
    version: int = 1,
    enabled: bool = True,
    status: str = "draft",
    description: Optional[str] = None,
    updated_by: Optional[str] = None,
    edited_by: Optional[str] = None,
    approved_by: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create or update a prompt template in Cosmos DB.

    Args:
        prompt_id:
            Cosmos DB document ID.

        rule_id:
            Partition-key value stored in the rule_id property.

        template:
            Complete prompt-template text.

        version:
            Prompt version number.

        enabled:
            Whether the application may use the prompt.

        status:
            Template status: draft, pending, approved, rejected.

        description:
            Optional prompt description.

        updated_by:
            User or process that updated the prompt.

        edited_by:
            User who last edited the template.

        approved_by:
            User who approved the template.

        metadata:
            Optional non-sensitive metadata.

    Returns:
        The saved Cosmos DB document.

    Important:
        The container partition-key path is /rule_id.

        After a Cosmos document is created, its partition-key value
        should not be changed. To use a different rule_id, create a
        new document.
    """

    normalized_prompt_id = _validate_prompt_id(
        prompt_id
    )

    normalized_rule_id = _validate_rule_id(
        rule_id
    )

    normalized_template = _validate_template(
        template
    )

    normalized_version = _validate_version(
        version
    )

    if not isinstance(enabled, bool):
        raise TypeError(
            "enabled must be a boolean."
        )

    if description is not None and not isinstance(
        description,
        str,
    ):
        raise TypeError(
            "description must be a string or None."
        )

    if updated_by is not None and not isinstance(
        updated_by,
        str,
    ):
        raise TypeError(
            "updated_by must be a string or None."
        )

    if metadata is not None and not isinstance(
        metadata,
        dict,
    ):
        raise TypeError(
            "metadata must be a dictionary or None."
        )

    current_timestamp = _utc_timestamp()

    created_at = current_timestamp

    # Preserve created_at when updating an existing document.
    try:
        existing_document = _prompt_container.read_item(
            item=normalized_prompt_id,
            partition_key=normalized_rule_id,
        )

        existing_created_at = existing_document.get(
            "created_at"
        )

        if (
            isinstance(existing_created_at, str)
            and existing_created_at.strip()
        ):
            created_at = existing_created_at

    except CosmosResourceNotFoundError:
        # This is a new document.
        pass

    except CosmosHttpResponseError as exc:
        raise PromptStoreError(
            f"Could not check whether prompt "
            f"'{normalized_prompt_id}' already exists. "
            f"Status code: {exc.status_code}."
        ) from exc

    prompt_document: Dict[str, Any] = {
        "id": normalized_prompt_id,
        "rule_id": normalized_rule_id,
        "document_type": "prompt_template",
        "template": normalized_template,
        "version": normalized_version,
        "enabled": enabled,
        "status": status.strip() if isinstance(status, str) and status.strip() else "draft",
        "description": (
            description.strip()
            if isinstance(description, str)
            else ""
        ),
        "created_at": created_at,
        "updated_at": current_timestamp,
        "updated_by": (
            updated_by.strip()
            if isinstance(updated_by, str)
            and updated_by.strip()
            else "application"
        ),
        "edited_by": (
            edited_by.strip()
            if isinstance(edited_by, str)
            and edited_by.strip()
            else ""
        ),
        "approved_by": (
            approved_by.strip()
            if isinstance(approved_by, str)
            and approved_by.strip()
            else ""
        ),
        "metadata": metadata or {},
    }

    try:
        saved_document = _prompt_container.upsert_item(
            body=prompt_document
        )

    except CosmosHttpResponseError as exc:
        raise PromptStoreError(
            f"Could not create or update prompt "
            f"'{normalized_prompt_id}' under rule_id "
            f"'{normalized_rule_id}'. "
            f"Status code: {exc.status_code}."
        ) from exc

    validated_document = _validate_prompt_document(
        document=saved_document,
        expected_prompt_id=normalized_prompt_id,
        expected_rule_id=normalized_rule_id,
        require_enabled=False,
    )

    # Remove any older cached version and cache the saved document.
    clear_prompt_cache(
        prompt_id=normalized_prompt_id,
        rule_id=normalized_rule_id,
    )

    _set_cached_prompt(
        prompt_id=normalized_prompt_id,
        rule_id=normalized_rule_id,
        document=validated_document,
    )

    logger.info(
        "Prompt template saved successfully. "
        "Prompt ID: %s, rule ID: %s, version: %d",
        normalized_prompt_id.replace('\n', '').replace('\r', ''),
        normalized_rule_id.replace('\n', '').replace('\r', ''),
        int(normalized_version),
    )

    return validated_document


def upsert_prompt_template_from_file(
    file_path: str,
    prompt_id: str,
    rule_id: str,
    version: int = 1,
    enabled: bool = True,
    description: Optional[str] = None,
    updated_by: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Read a UTF-8 text file and upload it as a prompt template.

    Example:

        result = upsert_prompt_template_from_file(
            file_path="app/prompts/collections_follow_up_v1.txt",
            prompt_id="collections-followup-v1",
            rule_id="collections-followup",
            version=1,
            enabled=True,
        )
    """

    if not isinstance(file_path, str):
        raise TypeError(
            "file_path must be a string."
        )

    if not file_path.strip():
        raise ValueError(
            "file_path cannot be empty."
        )

    normalized_path = Path(
        file_path.strip()
    ).expanduser().resolve()

    if not normalized_path.exists():
        raise FileNotFoundError(
            f"Prompt-template file was not found: "
            f"{normalized_path}"
        )

    if not normalized_path.is_file():
        raise ValueError(
            f"The prompt-template path is not a file: "
            f"{normalized_path}"
        )

    try:
        template = normalized_path.read_text(
            encoding="utf-8"
        )

    except UnicodeDecodeError as exc:
        raise PromptStoreError(
            f"Prompt-template file must use UTF-8 encoding: "
            f"{normalized_path}"
        ) from exc

    except OSError as exc:
        raise PromptStoreError(
            f"Unable to read prompt-template file: "
            f"{normalized_path}"
        ) from exc

    if not template.strip():
        raise ValueError(
            f"Prompt-template file is empty: "
            f"{normalized_path}"
        )

    return upsert_prompt_template(
        prompt_id=prompt_id,
        rule_id=rule_id,
        template=template,
        version=version,
        enabled=enabled,
        description=description,
        updated_by=updated_by,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# DELETE OPERATION
# ---------------------------------------------------------------------------

def delete_prompt_template(
    prompt_id: str,
    rule_id: str,
) -> None:
    """
    Delete a prompt document from Cosmos DB.

    Example:

        delete_prompt_template(
            prompt_id="collections-followup-v1",
            rule_id="collections-followup",
        )
    """

    normalized_prompt_id = _validate_prompt_id(
        prompt_id
    )

    normalized_rule_id = _validate_rule_id(
        rule_id
    )

    try:
        _prompt_container.delete_item(
            item=normalized_prompt_id,
            partition_key=normalized_rule_id,
        )

    except CosmosResourceNotFoundError as exc:
        raise PromptNotFoundError(
            f"Prompt document '{normalized_prompt_id}' "
            f"was not found under rule_id partition "
            f"'{normalized_rule_id}'."
        ) from exc

    except CosmosHttpResponseError as exc:
        raise PromptStoreError(
            f"Could not delete prompt "
            f"'{normalized_prompt_id}'. "
            f"Status code: {exc.status_code}."
        ) from exc

    clear_prompt_cache(
        prompt_id=normalized_prompt_id,
        rule_id=normalized_rule_id,
    )

    logger.info(
        "Prompt template deleted successfully. "
        "Prompt ID: %s, rule ID: %s",
        normalized_prompt_id.replace('\n', '').replace('\r', ''),
        normalized_rule_id.replace('\n', '').replace('\r', ''),
    )


# ---------------------------------------------------------------------------
# OPTIONAL LOCAL TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        result = test_cosmos_connection()

        print("Cosmos DB connection successful.")
        print(f"Database: {result['database']}")
        print(f"Container: {result['container']}")
        print(
            "Partition-key paths:",
            result["partition_key_paths"],
        )

        if "/rule_id" not in result["partition_key_paths"]:
            print(
                "WARNING: The container partition key is not /rule_id."
            )
    except PromptStoreError as exc:
        print(f"Cosmos DB connection failed: {exc}")
        raise
        