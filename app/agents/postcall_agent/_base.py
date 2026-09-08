"""
postcallagent/_base.py
======================
Shared infrastructure imported by all 8 action-detection tools.

Every act00X file needs ONLY:
  - A Pydantic result model  (fields specific to that action)
  - A SYSTEM_PROMPT string
  - _OUTPUT_FIELDS list + optional _DEFAULTS dict
  - One-line run() that calls run_tool()

Nothing else. No LLM wiring, no prompt templates, no token callbacks.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Type, TypeVar

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Token usage callback
# ---------------------------------------------------------------------------

class TokenUsageCallback(BaseCallbackHandler):
    """Accumulates token counts from one or many LLM calls."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        if response.llm_output and "token_usage" in response.llm_output:
            usage = response.llm_output["token_usage"]
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_tokens += usage.get("total_tokens", 0)

    def to_dict(self) -> dict:
        # gpt-4o pricing: $2.50/1M prompt tokens, $10.00/1M completion tokens
        prompt_cost = (self.prompt_tokens / 1_000_000) * 2.50
        completion_cost = (self.completion_tokens / 1_000_000) * 10.00
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(prompt_cost + completion_cost, 6),
        }


# ---------------------------------------------------------------------------
# Shared LLM factory
# ---------------------------------------------------------------------------

def build_llm() -> AzureChatOpenAI:
    """Create AzureChatOpenAI from environment variables. Called once per request."""
    return AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_GPT4_API_ENDPOINT", os.getenv("IND_AZURE_ENDPOINT", " ")),
        api_key=os.getenv("AZURE_GPT4_API_KEY", os.getenv("IND_AZURE_API_KEY", " ")),
        azure_deployment=os.getenv("AZURE_GPT4_API_DEPLOYMENT", "gpt-5.4"),
        api_version=os.getenv("AZURE_GPT4_API_VERSION", "2025-01-01-preview"),
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Shared human message template
# ---------------------------------------------------------------------------

HUMAN_TEMPLATE = "Customer Name: {user_name}\nTranscript:\n{transcript}"


# ---------------------------------------------------------------------------
# Base result model — every act model inherits from this
# ---------------------------------------------------------------------------

class BaseActionResult(BaseModel):
    """Minimum fields every action result must declare."""
    found: bool
    action_id: str
    action_type: str
    confidence_score: float
    confidence_reason:  str
    special_notes: str
    Delay_reaon:str

# ---------------------------------------------------------------------------
# Generic tool runner — the single engine used by every act00X file
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseActionResult)


async def run_tool(
    result_model: Type[T],
    system_prompt: str,
    user_name: str,
    transcript: str,
    output_fields: list[str],
    field_defaults: Optional[dict] = None,
    token_cb: Optional[TokenUsageCallback] = None,
) -> Optional[dict]:
    """
    Generic action-tool runner shared by all 8 act files.

    Parameters
    ----------
    result_model   : Pydantic model class for structured LLM output.
    system_prompt  : Specialist system prompt for this action.
    user_name      : Customer name from call data.
    transcript     : Call transcript text.
    output_fields  : Fields to include in the returned dict (per ACTION_SCHEMA).
    field_defaults : Field → default value when field is None/falsy.
                     e.g. {"delivery_channel": "Email", "escalation_level": "L1"}
    token_cb       : Shared TokenUsageCallback; a local one is created if None.

    Returns
    -------
    dict  if the action was detected in the transcript.
    None  if the action was not detected.
    """
    cb = token_cb or TokenUsageCallback()
    llm = build_llm().with_structured_output(result_model)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", HUMAN_TEMPLATE),
    ])
    result: T = await (prompt | llm).ainvoke(
        {"user_name": user_name, "transcript": transcript},
        config={"callbacks": [cb]},
    )

    if not result.found:
        return None

    defaults = field_defaults or {}
    raw = result.model_dump()
    out: dict = {}

    for field in output_fields:
        val = raw.get(field)
        if field in ["action_type", "action_id"]:
            if(field in defaults):
                out[field] = defaults[field]
                continue
        if field == "documents_requested":
            out[field] = val if val is not None else []
        elif not val and field in defaults:
            out[field] = defaults[field]
        else:
            out[field] = val if val is not None else ""

    return out
 