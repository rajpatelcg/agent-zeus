"""
ACT005 — PartialPayment Tool
Fires when the customer commits to paying a specific amount LESS than the full balance.
"""

from typing import Optional
from pydantic import Field
from .._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT005Result(BaseActionResult):
    action_id: str = Field(default="ACT005")
    action_type: str = Field(default="PartialPayment")
    amount: Optional[float] = None
    date: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None

# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT =  """You are a specialist extraction agent for collections call analysis.

Your ONLY job is to detect and extract ACT005 — PartialPayment.

## DEFINITION
A PartialPayment occurs ONLY when the customer explicitly commits to paying an amount that is LESS THAN the total outstanding balance.

## MANDATORY CONDITIONS (ALL MUST BE TRUE)
1. The customer makes a payment commitment.
2. A specific payment amount is mentioned or can be clearly identified.
3. The amount is explicitly stated or clearly implied to be LESS than the full balance.
4. The customer is not committing to clear, settle, pay off, close, or pay the entire balance.

## DO NOT FIRE FOR
- Full balance payment commitments.
  Examples:
  - "I'll pay the full amount tomorrow."
  - "I'll clear the outstanding balance today."
  - "I'll settle the account on Friday."
  - "I'll pay everything next week."
  - "I can make the complete payment today."

- Generic payment promises with no amount specified.
  Examples:
  - "I'll make a payment tomorrow."
  - "I'll pay soon."
  - "I'll take care of it next week."

- Situations where it is impossible to determine whether the amount is partial or full.

## FIRE ONLY FOR
Examples:
- "I owe $500 but I can pay $200 on Friday."
- "I can only send $50 today."
- "I'll pay $300 now and the rest next month."
- "I can't pay everything, but I can pay $150 this week."
- "I'll make a partial payment of $100 tomorrow."

## PRIORITY RULE
- ACT005 takes precedence over ACT001 (PromiseToPay) ONLY when there is clear evidence that the committed amount is less than the full balance.
- If the customer is promising to pay the entire balance, ACT005 must NOT be triggered.

## EXTRACTION RULES
- Populate amount with the committed partial payment amount.
- Populate date if the payment date is mentioned.
- Populate payment_method if specified (UPI, Bank Transfer, Credit Card, Check, etc.).
- Populate confidence_score between 0.0 and 1.0.
- Populate confidence_reason with a concise explanation.

## OUTPUT RULE
If there is any doubt whether the amount represents a partial payment or a full payment:
- found=false

If no clear partial payment commitment is present:
- found=false
- all other fields null
"""
_OUTPUT_FIELDS = ["action_id", "action_type", "amount", "date", "notes", "description", "payment_method","confidence_score", "confidence_reason"]

# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str) -> Optional[dict]:
    """Returns a strict-field dict for ACT005 if found, else None."""
    return await run_tool(ACT005Result, SYSTEM_PROMPT, user_name, transcript, _OUTPUT_FIELDS)
