"""
ACT007 — CreditRequest Tool
Fires when the customer asks for a discount/credit OR makes payment conditional on receiving one.
"""

from typing import Optional
from pydantic import Field
from .._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT007Result(BaseActionResult):
    action_id: str = Field(default="ACT007")
    action_type: str = Field(default="CreditRequest")
    notes: Optional[str] = None
    description: Optional[str] = None
    requested_amount: Optional[float] = None
    reason: Optional[str] = None
    requested_date: Optional[str] = None

# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a specialist extraction agent for collections call analysis.
Your ONLY job: detect and extract ACT007 — CreditRequest.

## STRICT RULES:
- FIRE (found=true) whenever a customer:
  (a) Directly asks for a discount, credit, adjustment, or balance reduction.
      Examples: "give me a 20% credit", "reduce the balance", "can you waive the late fee?"
  (b) Makes payment CONDITIONAL on receiving a credit or discount.
      Examples: "I'll pay IF you give me a discount", "I can pay only if you give me 20% credit."
- CRITICAL: Conditional payment statements ALWAYS trigger ACT007, even if the customer later agrees to something else.
- Populate requested_amount if a specific dollar amount or percentage is mentioned.
- Populate reason with the customer's justification (e.g., "service not delivered", "billing error").
- Populate requested_date if the customer specifies a deadline for the credit.
- Populate confidence with a range between 0.0 and 1.0 representing how strong the evidence is for this action.
- Populate confidence_reason with a concise reason for the score.
If no credit/discount request or conditional payment is present → found=false, all other fields null.
- Populate confidence_score 0.0
- Populate confidence_reason with a concise reason for the score.
- Populate all fields as null."""
_OUTPUT_FIELDS = ["action_id", "action_type", "notes", "description",
                  "requested_amount", "reason", "requested_date","confidence_score", "confidence_reason"]

# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str) -> Optional[dict]:
    """Returns a strict-field dict for ACT007 if found, else None."""
    return await run_tool(ACT007Result, SYSTEM_PROMPT, user_name, transcript, _OUTPUT_FIELDS)
