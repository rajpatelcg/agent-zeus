"""
ACT009-UpdateContactInfo
Fires for any customer request NOT covered by ACT001–ACT008.
Examples: invoice number inquiry, account details, address updates, general account questions.
"""

from typing import Optional
from pydantic import Field
from .._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT009Result(BaseActionResult):
    action_id: str = Field(default="ACT009")
    action_type: str = Field(default="OtherCustomerRequest")
    notes: Optional[str] = None
    description: Optional[str] = None
    request_details: Optional[str] = None
    preferred_time: Optional[str] = None

# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a specialist extraction agent for collections call analysis.
Your ONLY job: detect and extract ACT009 — OtherCustomerRequest.

## STRICT RULES:
- FIRE (found=true) for any customer request that is NOT covered by ACT001–ACT008.
- Examples of ACT009 requests:
  - Updating the customers contanct information (address, email, phone) if there is a difference in the contact information in the system and the user provides the correct contact details then update the contact information and fire ACT009.
  

If no uncategorized customer request is present → found=false, all other fields null."""

_OUTPUT_FIELDS = ["action_id", "action_type", "notes", "description",
                  "request_details", "preferred_time"]

# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str) -> Optional[dict]:
    """Returns a strict-field dict for ACT009 if found, else None."""
    return await run_tool(ACT009Result, SYSTEM_PROMPT, user_name, transcript, _OUTPUT_FIELDS)
