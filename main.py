import uvicorn
from typing import Dict, Any
from fastapi import FastAPI
import sys
import asyncio
import logging
from contextlib import asynccontextmanager
import uvicorn
from typing import Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import router as api_router, initiate_outbound_call
from app.config.src import SERVER_HOST, SERVER_PORT
# from app.caller_features.sharepoint import list_sharepoint_folder_contents
# from app.cosmos.db import fetch_low_confidence_records_from_cosmos,approved_records

from azure_service_bus.config import AZURE_SERVICE_BUS_CONN_STR
import os
from app.config.src import (
    WEBHOOK_URL,
    account_sid,
    auth_token
)
from twilio.rest import Client
from app.agents.precall_agent.prompt_template_routes import (
    router as prompt_template_router,
)

# from azure_service_bus.worker import run_agentic_consumer

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     bg_task = None
#     if AZURE_SERVICE_BUS_CONN_STR:
#         print("[Lifespan] Starting background Azure Service Bus consumer task...")
#         bg_task = asyncio.create_task(run_agentic_consumer())
#     else:
#         print("[Lifespan] AZURE_SERVICE_BUS_CONN_STR not found. Service Bus worker not started.")
#     try:
#         yield
#     finally:
#         if bg_task:
#             print("[Lifespan] Cancelling background Azure Service Bus consumer task...")
#             bg_task.cancel()
#             try:
#                 await bg_task
#             except asyncio.CancelledError:
#                 pass
#             print("[Lifespan] Background Service Bus task cancelled cleanly.")



import httpx
import logging

logger = logging.getLogger(__name__)

active_assignments = {}

ORCHESTRATOR_URL = os.getenv("ORCHESTRATION_SERVICE_URL")
AGENT_ID = os.getenv("AGENT_ID")

print(f"I AM USING AGENT ID : {AGENT_ID}")



def resync_call(assignment):
   
    call_sid  = assignment.get("call_sid")
    print("RESYNCING_CALL := ", call_sid)
    if(not call_sid): return
    client = Client(account_sid, auth_token)
    ws_url = WEBHOOK_URL.replace("https://", "wss://").replace("http://", "ws://")
    client.calls(call_sid).update(
                twiml=f"""
            <Response>
            <Start>
                <Stream url="{ws_url}/api/ws" />
            </Start>
            </Response>
            """
        )

async def sync_active_assignments():
    
    if not ORCHESTRATOR_URL:
        logger.warning("ORCHESTRATOR_URL not configured")
        return

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{ORCHESTRATOR_URL}/agents/{AGENT_ID}/assignments"
            )

            response.raise_for_status()

            data = response.json()
            
            print("Ëxisting assignments",)

            assignments = data.get("assignments", [])

            for assignment in assignments:
                call_id = assignment["call_id"]

                active_assignments[call_id] = {
                    "call_sid": assignment.get("call_sid"),
                    "assigned_at": assignment.get("assigned_at"),
                    "agent_id": assignment.get("agent_id")
                }
                # resync_call(assignment)

            logger.info(
                "Recovered %s active assignments from orchestrator",
                len(assignments)
            )

    except Exception as exc:
        logger.exception(
            "Failed to recover assignments from orchestrator: %s",
            exc
        )
        
        
@asynccontextmanager
async def lifespan(app: FastAPI):

    await sync_active_assignments()
    try:
        yield
    finally:
        pass


app = FastAPI(title="Collections Voice Agent API", lifespan=lifespan)            
# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the API router for Twilio webhook paths (/api/, /api/ws, /api/status-callback, /api/call)
app.include_router(api_router, prefix=f"/{AGENT_ID}/api")
app.include_router(

prompt_template_router

)
# Root-level alias so the orchestration layer can call /call directly (no prefix needed)
@app.post(f"/{AGENT_ID}/call")
async def call_root(request: Dict[str, Any]):
    """Root-level alias for /api/call — allows orchestration layer to POST to /call directly."""
    return await initiate_outbound_call(request)


@app.get(f"/{AGENT_ID}/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get(f"/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}

@app.get(f"/")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
# @app.get("/db")
# async def get_low_confidence_records(
#     threshold: float = Query(DEFAULT_CONFIDENCE_THRESHOLD),
#     limit: Optional[int] = Query(None)
# ):
  


#     records = await fetch_low_confidence_records_from_cosmos(
#         threshold=threshold,
#         limit=limit
#     )

#     return {
#         "threshold": threshold,
#         "count": len(records),
#         "records": records
#     }


# @app.post("/approve")
# async def approving(review):
#         approved_records = await approved_records(
#         threshold=threshold,
#         limit=limit
#     )
    
   
#     if approved_records["approved"]="approved":
     

#        return {
#        "updated approved records"
#     }
#     else:
#         return {
#             "not updated"
#         }

# @app.get("/sharepoint-list")
# async def get_sharepoint_list():
#     items = list_sharepoint_folder_contents(
#         "Accounts Receivable Collections/Agentic for Collections/Input1by1/test/"
#     )

#     if not items:
#         return {"items": []}

#     for item in items:
#         item_type = "Folder" if "folder" in item else "File"
#         print(f"{item_type}: {item['name']}")

#     return {"items": items}

if __name__ == "__main__":
    print(f"Starting server at http://{SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
    # #   uvicorn.run("app.api.endpoints:router", host="127.0.0.1", port=8000)
    # uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)