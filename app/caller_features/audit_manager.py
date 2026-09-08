from datetime import datetime
import asyncio
import aiohttp
import os
import json
from dotenv import load_dotenv
import threading
from typing import Optional, List, Dict, Any

# Load environment variables
load_dotenv()

# The URL for the audit log endpoint
log_endpoint = os.getenv("AUDIT_CONTAINER_URL")



async def log(user_data: dict, event_name: str, event_log_id: str, event_status: bool, reason_desc: str = "", event_type: str = "Call_Dailer"):
    """
    Sends activity logs to the orchestration layer.
    """
    if not log_endpoint or not log_endpoint.startswith("http"):
        # Silently return if no endpoint is configured to avoid breaking the main flow
        return {"error": "no_endpoint"}

    # Map the user_data to the payload format expected by the activity-log API
    # Strictly following the requested schema
    payload = {
        "call_id": str(user_data.get("call_id") or user_data.get("call_sid") or ""),
        "customer_id": str(user_data.get("customer_number") or user_data.get("customer_id") or ""),
        "customer_name": str(user_data.get("customer_name") or user_data.get("customer") or ""),
        "invoice_numbers": user_data.get("invoice_numbers") or user_data.get("invoice_number") or [inv.get("invoice_number") for inv in user_data.get("invoice_details", [])] or [],
        "event_log_id": str(event_log_id or ""),
        "event_name": str(event_name or ""),
        "event_type": str(event_type or "Call_Dailer"),
        "event_status": bool(event_status),
        "call_status": bool(user_data.get("call_status") == "success" or user_data.get("call_status") is True),
        "attempt_number": int(user_data.get("attempt_number") or user_data.get("attempt_no") or 1)
    }

    # Only add reason_desc if the event failed
    if not event_status and reason_desc:
        payload["reason_desc"] = str(reason_desc)

    print(f"[Audit] Sending {event_log_id} to {log_endpoint} | Status: {event_status}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(log_endpoint, json=payload, timeout=5) as response:
                resp_text = await response.text()
                if response.status in [200, 201]:
                    # print(f"[Audit] {event_log_id} sent successfully.")
                    return "logs_sent"
                
                print(f"[Audit] Failed to send {event_log_id}. Status: {response.status}, Response: {resp_text}")
                return {
                    "error": "failed_to_send_logs",
                    "status_code": response.status,
                    "response": resp_text
                }
    except Exception as e:
        print(f"[Audit] Exception sending {event_log_id}: {str(e)}")
        return {
            "error": "exception_occurred",
            "message": str(e)
        }

def fire_and_forget_log(user_data: dict, event_name: str, event_log_id: str, event_status: bool, reason_desc: str = "", event_type: str = "Call_Dailer"):
    """
    Logs an event without waiting for the response.
    """
    # Create a copy of user_data to avoid mutation issues in background thread
    data_copy = user_data.copy() if user_data else {}
    
    try:
        # Check if we are in an event loop
        loop = asyncio.get_running_loop()
        loop.create_task(log(data_copy, event_name, event_log_id, event_status, reason_desc, event_type))
    except RuntimeError:
        # No running event loop — run in a background thread
        threading.Thread(
            target=lambda: asyncio.run(log(data_copy, event_name, event_log_id, event_status, reason_desc, event_type)),
            daemon=True
        ).start()

# -----------------------------------------------------------------------------
# Test Execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    test_user_data = {
        "call_id": "test_call_id_123",
        "customer_number": "test_cust_456",
        "customer_name": "Test User",
        "attempt_number": 1,
        "call_status": True
    }

    async def main():
        print(f"Testing log endpoint: {log_endpoint}")
        res = await log(test_user_data, "Test Event", "callhandler_e001", True, "", "Call_Dailer")
        print(f"Result: {res}")

    asyncio.run(main())
    


'''
Testing pending...
'''
