"""
ACT010 — InvoiceCopyCreditMemo Tool

Fires when the customer requests an invoice copy, invoice document,
credit memo, or credit note to be sent to them via any channel.
"""

from typing import List, Optional
from pydantic import Field
from .._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT010Result(BaseActionResult):
    action_id: str = Field(default="ACT010")
    action_type: str = Field(default="InvoiceCopyCreditMemo")
    notes: Optional[str] = None
    description: Optional[str] = None
    documents_requested: Optional[List[str]] = None
    document_type: Optional[str] = None
    delivery_channel: Optional[str] = None

# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a specialist extraction agent for collections call analysis.
Your ONLY job: detect and extract ACT010 — InvoiceCopyCreditMemo.

## STRICT RULES:
- FIRE (found=true) when the customer REQUESTS that an invoice, invoice copy, duplicate invoice, credit memo, or credit note be SENT to them via email, post, portal, or any channel.
- Examples:
  - "Can you send me the invoice?"
  - "Please email me the invoice copy."
  - "Send me the duplicate invoice."
  - "Can you send the credit memo?"
  - "Please share the credit note."
- IMPORTANT: This is based on the CUSTOMER'S REQUEST — not whether the agent fulfilled it.
  Even if the agent says "I cannot send it", the customer's request MUST still be ACT010.
- DO NOT FIRE for Statement of Account / SOA requests. Those belong to ACT011.
- DO NOT FIRE if the customer only asks for an invoice number, invoice date, balance, or amount without asking for the document to be sent.
- Default delivery_channel to 'Email' unless another channel is specified.
- Populate documents_requested as a list of each specific document mentioned.
- document_type should be one of:
  - "Invoice"
  - "Credit Memo"
  - "Credit Note"
  - "Invoice and Credit Memo"
  - null if unclear
- Populate confidence_score with a range between 0.0 and 1.0 representing how strong the evidence is for this action.
- Populate confidence_reason with a concise reason for the score.

If no invoice, invoice copy, credit memo, or credit note send request is present → found=false, all other fields null."""

_OUTPUT_FIELDS = [
    "action_id",
    "action_type",
    "notes",
    "description",
    "documents_requested",
    "document_type",
    "delivery_channel",
    "confidence",
    "confidence_reason",
]

_DEFAULTS = {
    "delivery_channel": "Email"
}

# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str):
    """Returns a strict-field dict for ACT010 if found, else None."""
    return await run_tool(
        ACT010Result,
        SYSTEM_PROMPT,
        user_name,
        transcript,
        _OUTPUT_FIELDS,
        _DEFAULTS,
    )