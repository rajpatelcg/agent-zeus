"""
ACT004 — DocumentCopy Tool

Fires when the customer requests that an invoice copy, invoice document,
credit memo, or credit note be sent to them via any channel.
"""

from typing import List, Optional
from pydantic import Field
from app.agents.postcall_agent._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT004Result(BaseActionResult):
    action_id: str = Field(default="ACT004")
    action_type: str = Field(default="DocumentCopy")
    notes: Optional[str] = None
    description: Optional[str] = None
    documents_requested: Optional[List[str]] = None
    document_type: Optional[str] = None
    delivery_channel: Optional[str] = None

# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a specialist extraction agent for collections call analysis.
Your ONLY job: detect and extract ACT004 — DocumentCopy.

## STRICT RULES:
- FIRE (found=true) when the customer REQUESTS that an invoice, invoice copy, duplicate invoice, invoice document, credit memo, or credit note be SENT to them via email, post, portal, or any channel.

- This action is ONLY for document copy requests related to:
  - Invoice
  - Invoice copy
  - Duplicate invoice
  - Invoice document
  - Credit memo
  - Credit memo copy
  - Credit note
  - Credit note copy

## EXAMPLES THAT SHOULD FIRE:
- "Can you send me the invoice?"
- "Please email me the invoice copy."
- "Can I get a duplicate invoice?"
- "Send me the invoice document."
- "Can you share the credit memo?"
- "Please send the credit note to my email."
- "I need copies of the invoice and credit memo."
- "Can you send the invoice again?"

## IMPORTANT:
- Detection is based on the CUSTOMER'S REQUEST — not whether the agent fulfilled it.
- Even if the agent says "I cannot send it", "you need to download it from the portal", or "we already sent it", the customer's request MUST still be captured as ACT004 if they asked for the invoice or credit memo document to be sent.
- Do NOT fire just because the agent mentions sending an invoice or credit memo. The customer must request it.

## DO NOT FIRE:
- Do NOT fire for Statement of Account, SOA, account statement, ledger statement, customer statement, or balance statement requests.
  These belong to ACT010 — StatementOfAccount.
- Do NOT fire if the customer only asks about an invoice number, invoice date, invoice amount, due date, balance, or payment status without asking for the invoice document/copy to be sent.
  Example: "What is the invoice number?" should NOT fire ACT004.
- Do NOT fire for payment confirmation, receipt, proof of payment, contract, purchase order, proof of delivery, or other non-invoice documents unless they are explicitly invoice or credit memo related.
- Do NOT fire for general account balance or open item questions unless the customer specifically requests an invoice copy, invoice document, credit memo, or credit note to be sent.

## RELATION TO OTHER ACTIONS:
- If the customer asks for an invoice number only, that should be handled by the invoice information action, not ACT004.
- If the customer asks for an invoice copy to be emailed/sent/shared, that is ACT004.
- If the customer asks for a Statement of Account / SOA to be sent, that is ACT010, not ACT004.
- If the customer asks for both an invoice copy and a Statement of Account, ACT004 should capture only the invoice/credit memo request, while ACT010 should separately capture the Statement of Account request.


- Populate document_type based on the requested document:
  - "invoice" for invoice, invoice copy, duplicate invoice, or invoice document
  - "credit_memo" for credit memo or credit memo copy
  - null if unclear

- Populate confidence_score with a range between 0.0 and 1.0 representing how strong the evidence is for this action.
- Populate confidence_reason with a concise reason for the score.

If no invoice copy, invoice document, credit memo, or credit note send request is present → found=false, all other fields null."""

_OUTPUT_FIELDS = [
    "action_id",
    "action_type",
    "notes",
    "description",
    "documents_requested",
    "document_type",
    "delivery_channel",
    "confidence_score",
    "confidence_reason",
]

_DEFAULTS = {
    "delivery_channel": "Email",
    "action_id": "ACT004",
    "action_type" : "DocumentCopy"
}

# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str):
    """Returns a strict-field dict for ACT004 if found, else None."""
    return await run_tool(
        ACT004Result,
        SYSTEM_PROMPT,
        user_name,
        transcript,
        _OUTPUT_FIELDS,
        _DEFAULTS,
    )

if __name__ == "__main__":


  import json

  with open("C:/Users/rajrames/Desktop/collection_new_v2/transcripts/invoice_copy.json", "r", encoding="utf-8") as file:
      transcript = json.load(file)

  print(transcript)
 