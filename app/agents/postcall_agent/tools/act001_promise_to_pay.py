"""
ACT001 — PromiseToPay Tool
Fires when the customer makes a clear, committed promise to pay the FULL balance by a specific date.
"""

from typing import Optional
from pydantic import Field
from .._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT001Result(BaseActionResult):
    action_id: str = Field(default="ACT001")
    action_type: str = Field(default="PromiseToPay")
    amount: Optional[float] = None
    date: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    delivery_channel: Optional[str] = None
    promise_to_pay_amount: Optional[str] = None
    promise_to_pay_date: Optional[str] = None
    payment_method: Optional[str] = None

# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a specialist extraction agent for collections call analysis.

Your ONLY job: detect and extract ACT001 — PromiseToPay.

## STRICT RULES:
- FIRE (found=true) ONLY when customer makes a CLEAR, SPECIFIC, COMMITTED promise to pay the FULL outstanding balance.
- The customer MUST name a specific date. E.g., "I'll pay the full $1,220 by Friday."
- DO NOT fire for vague statements: "I might pay soon", "maybe later", "I'll try", "in 2-3 months."
- DO NOT fire if payment is conditional on a credit/discount → that is ACT007.
- DO NOT fire if the customer is paying a partial amount → that is ACT005.

If criteria not met:
- found=false
- Populate confidence with 0.0
- Populate confidence_reason with a concise reason for the score.
- Populate all fields as null.

If found=true populate:
- amount
- date
- promise_to_pay_amount
- promise_to_pay_date(iso format)
- notes (verbatim from transcript)
- description
- delivery_channel (default 'Email')
- payment_method
- Populate confidence with range 0.0 to 1 representing how strong the evidence is for this action.
- Populate confidence_reason with a concise reason for the score.

Additional extraction rules:
- promise_to_pay_amount = exact amount the customer promises to pay.
- promise_to_pay_date = exact date the customer promises to pay.
- If found=false, both promise_to_pay_amount and promise_to_pay_date must be null.
"""


_OUTPUT_FIELDS = [
    "action_id",
    "action_type",
    "amount",
    "date",
    "promise_to_pay_amount",
    "promise_to_pay_date",
    "notes",
    "description",
    "delivery_channel",
    "payment_method",
    "confidence_score",
    "confidence_reason",
]

_DEFAULTS = {
    "delivery_channel": "Email"
}


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str) -> Optional[dict]:
    """Returns a strict-field dict for ACT001 if found, else None."""
    return await run_tool(
        ACT001Result,
        SYSTEM_PROMPT,
        user_name,
        transcript,
        _OUTPUT_FIELDS,
        _DEFAULTS,
    )