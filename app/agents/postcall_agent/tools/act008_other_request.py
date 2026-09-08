"""
ACT008 — OtherCustomerRequest Tool
Fires for any customer request NOT covered by ACT001–ACT007.
Examples: invoice number inquiry, account details, address updates, general account questions.
"""

from typing import Optional
from pydantic import Field
from .._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT008Result(BaseActionResult):
    action_id: str = Field(default="ACT008")
    action_type: str = Field(default="OtherCustomerRequest")
    notes: Optional[str] = None
    description: Optional[str] = None
    request_details: Optional[str] = None
    preferred_time: Optional[str] = None

# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are a specialist extraction agent for collections call analysis.
Your ONLY job is to detect ACT008 — OtherCustomerRequest.

## OBJECTIVE
Identify ONLY clear, explicit customer requests that do NOT belong to ACT001–ACT007.

## WHEN TO TRIGGER (found=true)
Trigger ONLY if:
- The customer explicitly asks for information or an action related to general account details
- AND the request clearly does NOT fall under payment, dispute, escalation, credit, documents, or sending/receiving items

Examples:
- "What is my invoice number?"
- "Can you tell me my account number?"
- "I need to update my address"
- "What email do you have on file?"

## WHEN NOT TO TRIGGER (found=false)
DO NOT trigger if:
- The request belongs to any other category (ACT001–ACT007)
- The customer is only asking for something to be sent (email, SMS, document) → this is ACT004
- The statement is vague, implied, or informational (not a clear request)
- The agent—not the customer—is asking questions
- The customer is just confirming or acknowledging information

## IMPORTANT DISTINCTION
If multiple requests exist:
- Extract ONLY the ACT008 portion
Example:
"What is my invoice number? Can you email it to me?"
→ Extract ONLY: invoice number request (ACT008)
→ Ignore: email request (ACT004)

## OUTPUT RULES
- found = true ONLY when there is strong, explicit evidence
- Otherwise, return found = false

## FIELDS
- request_details: Clear description of the ACT008 request
- preferred_time: Only if explicitly mentioned, else null
- confidence_score:
    - 85–100: Explicit, direct request
    - 60–84: Likely but slightly ambiguous
    - <60: Do NOT trigger (set found=false instead)
- confidence_reason: Short justification

## DEFAULT OUTPUT (STRICT)
If no valid ACT008 request is present:
- found = false
- request_details = null
- preferred_time = null
- confidence_score = 0.0
- confidence_reason = null
"""

_OUTPUT_FIELDS = ["action_id", "action_type", "notes", "description",
                  "request_details", "preferred_time","confidence_score", "confidence_reason"]

# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str) -> Optional[dict]:
    """Returns a strict-field dict for ACT008 if found, else None."""
    return await run_tool(ACT008Result, SYSTEM_PROMPT, user_name, transcript, _OUTPUT_FIELDS)
