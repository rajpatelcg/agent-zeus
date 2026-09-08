import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

logger = logging.getLogger("pipecat-voice-agent")

try:
    from azure.cosmos.aio import CosmosClient
    from azure.cosmos import PartitionKey
except Exception:
    CosmosClient = None
    PartitionKey = None


COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY = os.getenv("COSMOS_KEY")
COSMOS_CONVERSATION_DATABASE = os.getenv(
    "COSMOS_CONVERSATION_DATABASE",
    "voice-agent-db",
)
COSMOS_CONVERSATION_CONTAINER = os.getenv(
    "COSMOS_CONVERSATION_CONTAINER",
    "conversation-state",
)


class ConversationStateStore:
    """
    Cosmos DB backed conversation state store.

    Important behavior:
        - State is stored by user_data["call_sid"] when available.
        - If user_data["call_sid"] is present, caller may attempt resume.
        - If user_data["call_sid"] is not present, caller should proceed as a fresh call.

    Cosmos partition key:
        /call_sid
    """

    def __init__(self):
        self.enabled = bool(
            CosmosClient
            and COSMOS_ENDPOINT
            and COSMOS_KEY
        )
        self._client = None
        self._container = None

    async def _get_container(self):
        if not self.enabled:
            return None

        if self._container:
            return self._container

        self._client = CosmosClient(
            COSMOS_ENDPOINT,
            credential=COSMOS_KEY,
        )

        database = await self._client.create_database_if_not_exists(
            id=COSMOS_CONVERSATION_DATABASE
        )

        container = await database.create_container_if_not_exists(
            id=COSMOS_CONVERSATION_CONTAINER,
            partition_key=PartitionKey(path="/call_sid"),
        )

        self._container = container
        return self._container

    async def insert_initial_call_document(
        self,
        *,
        call_sid: str,
        call_id: str,
        user_data: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Inserts an initial conversation state document.

        Purpose:
            Create a starting Cosmos document for a fresh call.

        Document contains:
            - call_sid
            - call_id
            - is_resumed = False
            - empty messages
            - empty conversation
        """
        container = await self._get_container()

        if not container:
            logger.info(
                json.dumps(
                    {
                        "event_name": "conversation_initial_document_skipped",
                        "reason": "Cosmos DB configuration or package is unavailable.",
                        "call_sid": call_sid,
                        "call_id": call_id,
                    }
                )
            )
            return None

        user_data = user_data or {}

        document = {
            "id": call_sid,
            "call_sid": call_sid,
            "call_id": call_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "is_resumed": False,
            "current_language": None,
            "language_locked": False,
            "messages": [],
            "conversation": [],
            "call_success": None,
            "failure_message": None,
            "reason": "initial_call_document_created",
            "metadata": {
                "customer_name": user_data.get("customer_name"),
                "call_type": user_data.get("call_type"),
            },
            "user_data": user_data
        }

        try:
            await container.create_item(document)

            logger.info(
                json.dumps(
                    {
                        "event_name": "conversation_initial_document_created",
                        "call_sid": call_sid,
                        "call_id": call_id,
                    }
                )
            )

            return document

        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event_name": "conversation_initial_document_create_failed",
                        "call_sid": call_sid,
                        "call_id": call_id,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    }
                ),
                exc_info=True,
            )

            return None
    
    async def load_state(
        self,
        call_sid: str,
    ) -> Optional[Dict[str, Any]]:
        container = await self._get_container()

        if not container:
            logger.info(
                json.dumps(
                    {
                        "event_name": "conversation_state_store_disabled",
                        "reason": "Cosmos DB configuration or package is unavailable.",
                        "call_sid": call_sid,
                    }
                )
            )
            return None

        try:
            item = await container.read_item(
                item=call_sid,
                partition_key=call_sid,
            )
            return item

        except Exception as exc:
            logger.info(
                json.dumps(
                    {
                        "event_name": "conversation_state_not_found",
                        "call_sid": call_sid,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    }
                )
            )
            return None

    async def save_state(
        self,
        call_sid: str,
        user_data: dict,
        messages: List[dict],
        current_language: Optional[str],
        language_locked: bool,
        call_success: Optional[bool] = None,
        failure_message: Optional[str] = None,
        reason: str = "state_update",
    ) -> None:
        container = await self._get_container()

        if not container:
            return

        safe_messages = []

        for msg in messages or []:
            role = msg.get("role")
            content = msg.get("content")

            if role not in {"system", "user", "assistant"}:
                continue

            if not content:
                continue

            # Do not persist system prompts in Cosmos.
            # They can be regenerated and may be large.
            if role == "system":
                continue

            safe_messages.append(
                {
                    "role": role,
                    "content": str(content),
                }
            )

        max_messages = int(os.getenv("CONVERSATION_STATE_MAX_MESSAGES", "100"))
        safe_messages = safe_messages[-max_messages:]
        document = await self.load_state(call_sid=call_sid) or {}
  
        update = {
            "id": call_sid,
            "call_sid": call_sid,
            "call_id": user_data.get("call_id"),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "current_language": current_language,
            "language_locked": language_locked,
            "messages": safe_messages,
            "call_success": call_success,
            "failure_message": failure_message,
            "reason": reason
        }
        
        for f, v in update.items(): 
            document[f] = v


        try:
            await container.upsert_item(document)
            logger.info(
                json.dumps(
                    {
                        "event_name": "conversation_state_saved",
                        "call_sid": call_sid,
                        "message_count": len(safe_messages),
                        "reason": reason,
                    }
                )
            )

        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event_name": "conversation_state_save_failed",
                        "call_sid": call_sid,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    }
                ),
                exc_info=True,
            )

    async def close(self):
        try:
            if self._client:
                await self._client.close()
        except Exception:
            pass
    
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()