import asyncio
import json
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, Form, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from twilio.rest import Client
import uvicorn
from azure_service_bus.events import call_events
import logging
logger = logging.getLogger("endpoints")
import os
from app.config.src import (
    WEBHOOK_URL, 
    TWILIO_CALLBACK_EVENTS, 
    TWILIO_PHONE_NUMBER,
    account_sid,
    auth_token,
    active_calls, 
    blob_manager
)
# from app.caller_features.call_report import report_call_result
from app.caller_features.call_report import report_call_result
from app.caller_features.audit_manager import fire_and_forget_log
# from collection_new_v2.caller_bk import handle_voice_agent
from caller import handle_voice_agent
from app.caller_features.conversation_state_store import ConversationStateStore
conversation_state_store = ConversationStateStore()
# from caller_lang import handle_voice_agent


AGENT_ID = os.getenv("AGENT_ID")

router = APIRouter()



@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}

@router.post("/")
async def twilio_connect():
    """Twilio entry point: Establishes the WebSocket stream."""

    ws_url = WEBHOOK_URL.replace("https://", "wss://").replace("http://", "ws://")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream 
            url="{ws_url}/api/ws"
            statusCallback="{WEBHOOK_URL}/api/status-callback"
            statusCallbackMethod="POST" />
    </Connect>

    <Say voice="alice">
        Please wait while we reconnect you.
    </Say>

    <Pause length="5"/>

    <Redirect method="POST">
        {WEBHOOK_URL}/api/
    </Redirect>
</Response>"""

    return HTMLResponse(content=content, media_type="application/xml")

@router.get("/audio/{call_id}")
async def get_audio_playback(call_id: str):
    """Provides a temporary redirect to the audio SAS URL for playback."""
    sas_url = blob_manager.get_sas_url(call_id)
    if not sas_url:
        return {"error": "Recording not found"}
    return RedirectResponse(sas_url)




import httpx
import logging
import os

ORCHESTRATOR_URL = os.getenv("ORCHESTRATION_SERVICE_URL")
AGENT_ID = os.getenv("AGENT_ID")


async def update_call_sid(
    call_id: str,
    call_sid: str,
):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{ORCHESTRATOR_URL}/call_started",
                json={
                    "agent_id": AGENT_ID,
                    "call_id": call_id,
                    "call_sid": call_sid,
                },
            )

            response.raise_for_status()

            logger.info(
                "Updated call_sid=%s for call_id=%s",
                call_sid,
                call_id,
            )

    except Exception as exc:
        logger.exception(
            "Failed updating call_sid for call_id=%s : %s",
            call_id,
            exc,
        )
        
        

@router.post("/call")
async def initiate_outbound_call(request: Dict[str, Any]):
    """Initiates an outbound call via Twilio."""
    if not account_sid or not auth_token:
        fire_and_forget_log(request, "Pre‑Call Workflow Initialization", "callhandler_e001", False, "Pre‑call workflow could not be initialized due to missing or invalid inputs")
        return {"error": "Twilio credentials missing"}

    client = Client(account_sid, auth_token)
    user_phone = request.get("phone_number")
    if not user_phone:
        fire_and_forget_log(request, "Pre‑Call Workflow Initialization", "callhandler_e001", False, "Pre‑call workflow could not be initialized due to missing or invalid inputs")
        return {"error": "phone_number is required"}

    fire_and_forget_log(request, "Pre‑Call Workflow Initialization", "callhandler_e001", True)

    try:
        call = client.calls.create(
            from_=TWILIO_PHONE_NUMBER,
            to=user_phone,
            url=f"{WEBHOOK_URL}/api/",
            status_callback=f"{WEBHOOK_URL}/api/status-callback",
            status_callback_event=TWILIO_CALLBACK_EVENTS
        )
        # Store Twilio SID and ensure call_id is set (preserving user-provided ID if it exists)
        request["twilio_sid"] = call.sid
        if "call_id" not in request:
            request["call_id"] = call.sid
            
        active_calls[call.sid] = request
        call_id = request.get("call_id")
        async with ConversationStateStore() as store:
            await store.insert_initial_call_document(call_sid=call.sid, call_id=call_id, user_data=request)
        fire_and_forget_log(request, "Outbound Call Initiation", "callhandler_e003", True)
        return {"status": "Call initiated", "call_sid": call.sid}
    except Exception as e:
        fire_and_forget_log(request, "Outbound Call Initiation", "callhandler_e003", False, "Call could not be initiated due to telephony service or network failure")
        return {"error": str(e)}

# @router.post("/status-callback")
# async def twilio_status_callback(CallSid: str = Form(...), CallStatus: str = Form(...)):
#     """Handles Twilio status updates and triggers failure reporting if needed."""
#     user_data = active_calls.get(CallSid, {})
#     if CallStatus in ['failed', 'busy', 'no-answer', 'canceled']:
#         status_messages = {
#             'failed': "Call failed: technical error on telephony side",
#             'busy': "Customer busy: line was engaged",
#             'no-answer': "Customer did not pick the call",
#             'canceled': "Call canceled before connection"
#         }
#         msg = status_messages.get(CallStatus, f"Call unreachable: {CallStatus}")
#         fire_and_forget_log(user_data, "Outbound Call Termination", "callhandler_e004", False, f"Call ended unexpectedly: {msg}")
#         await report_call_result(CallSid, user_data, transcript=[], call_status=False, failure_message=msg)
#     elif CallStatus == 'completed':
#         if not user_data.get('ws_connected'):
#             msg = "Customer unreachable: call not answered or connection failed"
#             fire_and_forget_log(user_data, "Outbound Call Termination", "callhandler_e004", False, msg)
#             await report_call_result(CallSid, user_data, transcript=[], call_status=False, failure_message=msg)
#         else:
#             fire_and_forget_log(user_data, "Outbound Call Termination", "callhandler_e004", True)
#     return {"status": "received"}

@router.post("/status-callback")
async def twilio_status_callback(
    CallSid: str = Form(...),
    CallStatus: str = Form(...)
):
    """
    Handles Twilio status updates.

    Only report failures for actual telephony failures.
    Do NOT create a failed call record for 'completed'.
    The voice agent pipeline is responsible for determining
    success/failure once the conversation ends.
    """

    user_data = active_calls.get(CallSid, {})

    if CallStatus in ["failed", "busy", "no-answer", "canceled"]:

        status_messages = {
            "failed": "Call failed: Invalid number",
            "busy": "Customer busy: line was engaged",
            "no-answer": "Customer did not pick the call",
            "canceled": "Call canceled before connection",
        }

        msg = status_messages.get(
            CallStatus,
            f"Call unreachable: {CallStatus}"
        )

        fire_and_forget_log(
            user_data,
            "Outbound Call Termination",
            "callhandler_e004",
            False,
            f"Call ended unexpectedly: {msg}",
        )

        await report_call_result(
            CallSid,
            user_data,
            transcript=[],
            call_status=False,
            failure_message=msg,
        )

        event = call_events.pop(CallSid, None)
        if event:
            event.set()

    elif CallStatus == "completed":

        logger.info(
            "Call completed. Waiting for voice agent to finalize result. "
            f"CallSid={CallSid}"
        )

        fire_and_forget_log(
            user_data,
            "Outbound Call Termination",
            "callhandler_e004",
            True,
        )

        # Do NOT call report_call_result() here.
        # handle_voice_agent() already reports the final outcome
        # using telemetry, transcript, idle detection, etc.

        event = call_events.pop(CallSid, None)
        if event:
            event.set()

    return {"status": "received"}

# @router.websocket("/ws")
# async def websocket_endpoint(websocket: WebSocket):
#     """Main WebSocket handler for Twilio Media Stream."""
#     await websocket.accept()
#     try:
#         init_data = websocket.iter_text()
#         await init_data.__anext__()
#         call_data = json.loads(await init_data.__anext__())
        
#         stream_sid = call_data["start"]["streamSid"]
#         call_sid = call_data["start"]["callSid"]
        
#         if call_sid in active_calls:
#             active_calls[call_sid]['ws_connected'] = True
        
#         user_data = active_calls.get(call_sid, {})
#         await handle_voice_agent(websocket, stream_sid, call_sid, user_data)
        
#     except (WebSocketDisconnect, StopAsyncIteration):
#         fire_and_forget_log(active_calls.get(call_sid, {}), "Outbound Call Termination", "callhandler_e004", True)
#     except Exception as e:
#         print(f"WebSocket error: {e}")
#         fire_and_forget_log(active_calls.get(call_sid, {}), "Outbound Call Termination", "callhandler_e004", False, "Call ended unexpectedly due to connection drop or system interruption")



# from app.config.src import (
#     WEBHOOK_URL,
#     account_sid,
#     auth_token
# )
# import os

# from fastapi import FastAPI, Request, HTTPException
# from twilio.request_validator import RequestValidator

# validator = RequestValidator(auth_token)

debug_text = """
 __________________________________________
/ I'm sorry for what I said when I was     \
\ debugging.                               /
 ------------------------------------------
         \
          \
            \
              __
         oo  //\\
        (_ opl\/\\
          _/  \
        (_/\__/

"""
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    
    """
        Securing the WebSocket connection with Twilio's signature validation.
    """
    
    print(debug_text)
   
    print("Headers:")
    for k, v in websocket.headers.items():
        print(f"{k}: {v}")

    print("Signature:", websocket.headers.get("x-twilio-signature"))
    print("URL:", websocket.url)
    
    

    # Direct client IP and port
    if websocket.client:
        print("Client IP:", websocket.client.host)
        print("Client Port:", websocket.client.port)

    # Useful if behind a reverse proxy
    print("X-Forwarded-For:",
          websocket.headers.get("x-forwarded-for"))

    print("X-Real-IP:",
          websocket.headers.get("x-real-ip"))

    print("Origin:",
          websocket.headers.get("origin"))

    print("Host:",
          websocket.headers.get("host"))

    print("Signature:",
          websocket.headers.get("x-twilio-signature"))

    print("URL:",
          str(websocket.url))

    # twilio_signature = websocket.headers.get("x-twilio-signature")

    # if not twilio_signature:
    #     await websocket.close(code=1008)
    #     return

    # # validate here first

    # await websocket.accept()

    # ws_url = WEBHOOK_URL.replace("https://", "wss://").replace("http://", "ws://")
    # # Full URL that Twilio called
    # url = f"{ws_url}/api/ws"

    # # Validate request
    # is_valid = validator.validate(
    #     url,
    #     {},
    #     twilio_signature or ""
    # )

    # if not is_valid:
    #     print("Invalid Twilio signature. Closing WebSocket.")
    #     await websocket.close(code=1008)
    #     return

    await websocket.accept()
    try:
        init_data = websocket.iter_text()

        first = await init_data.__anext__()
        print("CONNECTED EVENT:")
        print(first)

        second = await init_data.__anext__()
        print("START EVENT:")
        print(json.dumps(json.loads(second), indent=2))

        call_data = json.loads(second)

        stream_sid = call_data["start"]["streamSid"]
        call_sid = call_data["start"]["callSid"]

        print(f"call_sid={call_sid}")
        print(f"stream_sid={stream_sid}")
        async with ConversationStateStore() as store:
            call_state = await store.load_state(call_sid)
        user_data =  None
        if(call_state):
            user_data = call_state.get("user_data")
        await handle_voice_agent(websocket, stream_sid, call_sid, user_data)

    except (WebSocketDisconnect, StopAsyncIteration):
         fire_and_forget_log(active_calls.get(call_sid, {}), "Outbound Call Termination", "callhandler_e004", True)
    except Exception as e:
         print(f"WebSocket error: {e}")
         fire_and_forget_log(active_calls.get(call_sid, {}), "Outbound Call Termination", "callhandler_e004", False, "Call ended unexpectedly due to connection drop or system interruption")

# --- EXAMPLE: HOW TO ADD A NEW ENDPOINT ---
# @router.get("/sharepoint-list")
# def get_sharepoint_list():
#     items = list_sharepoint_folder_contents(
#         # "Accounts Receivable Collections/Agentic for Collections/Input1by1/dev/"
#         "Accounts Receivable Collections/Agentic for Collections/Input1by1/test/"
#     )

#     if not items:
#         return {"items": []}

#     for item in items:
#         item_type = "Folder" if "folder" in item else "File"
#         print(f"{item_type}: {item['name']}")

#     return {"items": items}

# @router.get("/summary/{call_id}")
# async def get_call_summary(call_id: str):
#     """
#     Example of a new endpoint to fetch a call summary from your state.
#     """
#     call_info = active_calls.get(call_id)
#     if not call_info:
#         return {"error": "Call not found"}
#     return {"call_id": call_id, "summary": call_info.get("summary", "Pending...")}

if __name__ == "__main__":
   asyncio.run(initiate_outbound_call({
  "case_id": "09d8c94b-Collections-Mervin-five",
  "call_id": "09d8c94b-HARI-Collections-Mervin-four",
  "customer_number": "7f1e5e9d",
  "customer_name": "mervin Thomas",
  "billing_address": "6th street ,20th cross",
  "contact_name" : "MERVIN",
  "phone_number": "+919980979624",
  "email_address": "mervin.t@capgemini.com",
  "preferred_language": "en",
  "time_zone": "IST",
  "multiple_invoice": False,
  "call_type": 1,
  "attempt_no": 1,
  "call_data": "",
  "invoice_details": [
    {
      "invoice_number": "INV-SCN-006",
      "outstanding_balance": "1220.00",
      "overdue_status": "Open",
      "due_date": "2026-05-11T00:00:00",
      "days_past_due": 1,
      "purchase_order_num": "string",
      
      "closed_dispute_status": "string",
      "issued_credits": "1000.00"
    }
  ]
}))
   
   
    
 