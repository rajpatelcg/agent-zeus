"""
ACT002 — Escalation Tool
Fires when the customer requests or agrees to a callback from a senior/manager/supervisor.
"""

from typing import Optional
from pydantic import Field
from .._base import BaseActionResult, run_tool

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT002Result(BaseActionResult):
    action_id: str = Field(default="ACT002")
    action_type: str = Field(default="Escalation")
    notes: Optional[str] = None
    description: Optional[str] = None
    escalation_level: Optional[str] = None
    escalated_to_role: Optional[str] = None
    reason: Optional[str] = None
    target_resolution_date: Optional[str] = None
    sla_hours: Optional[int] = None
    due_by: Optional[str] = None
    
# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a specialist extraction agent for collections call analysis.
Your ONLY job: detect and extract ACT002 — Escalation.

## STRICT RULES:
- FIRE (found=true) when the customer requests OR agrees to a callback from a senior executive, manager, supervisor, or higher authority.
- Also fire when the agent escalates the call to a higher tier.
- Populate target_resolution_date and due_by with the specific callback date/time if mentioned.
- Default escalation_level to 'L1' if not explicitly specified.
- Populate escalated_to_role with the most specific role mentioned (e.g., "Manager", "Senior Collections Officer").
- Populate confidence with a range between 0.0 and 1.0 representing how strong the evidence is for this action.
- Populate confidence_reason with a concise reason for the score.
- Populate NPR(non payement reason or the payment refusal)
If no escalation is present → found=false, .
- Populate confidence_score with  0.0
- Populate no confidence_reason with a concise reason for the score.
- Populate all fields as null."""
_OUTPUT_FIELDS = ["action_id", "action_type", "notes", "description", "escalation_level",
                  "escalated_to_role", "reason", "target_resolution_date", "sla_hours", "due_by","confidence_score", "confidence_reason","npr_reason"]
_DEFAULTS = {"escalation_level": "L1"}

# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def run(user_name: str, transcript: str) -> Optional[dict]:
    """Returns a strict-field dict for ACT002 if found, else None."""
    return await run_tool(ACT002Result, SYSTEM_PROMPT, user_name, transcript, _OUTPUT_FIELDS, _DEFAULTS)
