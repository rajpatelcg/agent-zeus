"""
postcallagent/main.py — Orchestrator
======================================
Runs all 8 action-detection tools IN PARALLEL using asyncio.gather,
then assembles summary + categorization via a single meta-LLM call.

Package layout (postcallagent/):
  _base.py                      → Shared LLM factory, TokenUsageCallback, run_tool() engine
  main.py                       → This file — orchestrator entry point
  act001_promise_to_pay.py      → ACT001 PromiseToPay
  act002_escalation.py          → ACT002 Escalation
  act003_dispute.py             → ACT003 Dispute
  act004_document_copy.py       → ACT004 DocumentCopy
  act005_partial_payment.py     → ACT005 PartialPayment
  act006_doubtful_receivable.py → ACT006 DoubtfulReceivable
  act007_credit_request.py      → ACT007 CreditRequest
  act008_other_request.py       → ACT008 OtherCustomerRequest
"""

from __future__ import annotations

import asyncio
import json

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from ._base import TokenUsageCallback, build_llm, HUMAN_TEMPLATE
from .tools import (
    act001_promise_to_pay,
    act002_escalation,
    act003_dispute,
    act004_document_copy,
    act005_partial_payment,
    act006_doubtful_receivable,
    act007_credit_request,
    act008_other_request,
    act010_soa
)

load_dotenv()

# ---------------------------------------------------------------------------
# Meta-analysis: summary + action items + categorization (one LLM call)
# ---------------------------------------------------------------------------

class MetaAnalysis(BaseModel):
    summary: str = Field(description="A 2-3 sentence summary of the call transcript.")
    user_action: str = Field(description="The action item that the USER (customer) needs to take after the call.")
    agent_action_item: str = Field(description="The action item that the AGENT needs to take after the call.")
    category: str = Field(description="Categorize the outcome: 'pending', 'cleared', or 'dispute'.")
    confidence_score: float = Field(description="A confidence score between 0.0 and 1.0 for the analysis.")
    confidence_reason: str = Field(description="A short reason explaining the confidence score.")
    special_notes: str = Field(description="Any special notes from the customer, e.g., discount request or dispute mention. or incorrect contact information, or any other special notes that the customer mentioned during the call Don not include delay reason in this note.")
    delay_reason:str=Field(description="Delay reason: why there was a deplay in the payment")
_META_SYSTEM_PROMPT = """You are an AI assistant reviewing a collections call transcript.
Your job is to produce:
1. A 2-3 sentence summary covering: the main reason for the call, any payment agreements, and customer sentiment.
2. The action item the CUSTOMER must take after the call.
3. The action item the AGENT must take after the call.
4. The overall call outcome categorized as EXACTLY ONE of: 'pending', 'cleared', or 'dispute'.
5. A confidence score between 0 and 100 for the analysis.
6. if there are any special notes from the customer like if they are asking for a discount or if they are disputing the payment, please mention that in the summary as special notes.
7. "Delay_reaon":note the reason for delay in the payment  if mentioned or else keep it blank
8. A short confidence reason explaining the confidence score and the relevant Action item ids below are the relevant action item ids that the customer mentioned during the call. If there are no relevant action items, please mention that in the summary as special notes.
action item ids: ACT001, ACT002, ACT003, ACT004, ACT005, ACT006, ACT007, ACT008.
ACT001 PromiseToPay — need amount, date, invoices covered.
ACT002 PaymentAlreadySent — need reference/check/ACH, amount, release date, invoices covered.
ACT003 ExecutiveCallback — need reason, date, time, best contact.
ACT004 DocumentCopy — need document type, invoice numbers/all, delivery channel, confirmed email/address.
ACT005 BillingDispute — need disputed invoice, reason, disputed/undisputed amount.
ACT006 MissingPO — need affected invoices, PO detail, expected payment date.
ACT007 ContactUpdate — need field, new value, who confirmed. Do not claim update; say you will note it.
ACT008 RefusalOrNoPromise — need reason, dispute or not, escalation needed.

"""

async def _run_meta_analysis(
    user_name: str, transcript: str, token_cb: TokenUsageCallback
) -> MetaAnalysis:
    """Single LLM call for summary, action items, and category."""
    llm = build_llm().with_structured_output(MetaAnalysis)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _META_SYSTEM_PROMPT),
        ("human", HUMAN_TEMPLATE),
    ])
    return await (prompt | llm).ainvoke(
        {"user_name": user_name, "transcript": transcript},
        config={"callbacks": [token_cb]},
    )


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

async def process_transcript_and_update_json(call_data: dict) -> dict:
    """
    Runs all 8 action tools + meta-analysis IN PARALLEL, then assembles
    the final enriched call_data dict.

    ACT006 (DoubtfulReceivable) is always placed first in the actions list
    per business rules, even when found alongside other actions.
    """
    transcript = call_data.get("transcript", "")
    if isinstance(transcript, (dict, list)):
        transcript = json.dumps(transcript)

    user_name: str = call_data.get("user_name", "Unknown")
    token_cb = TokenUsageCallback()

    # Fan-out: all 8 action tools + meta-analysis run simultaneously
    (
        act001_result,
        act002_result,
        act003_result,
        act004_result,
        act005_result,
        act006_result,
        act007_result,
        act008_result,
        act010_result,
        meta_result,
    ) = await asyncio.gather(
        act001_promise_to_pay.run(user_name, transcript),
        act002_escalation.run(user_name, transcript),
        act003_dispute.run(user_name, transcript),
        act004_document_copy.run(user_name, transcript),
        act005_partial_payment.run(user_name, transcript),
        act006_doubtful_receivable.run(user_name, transcript),
        act007_credit_request.run(user_name, transcript),
        act008_other_request.run(user_name, transcript),
        act010_soa.run(user_name, transcript),
        _run_meta_analysis(user_name, transcript, token_cb),
        return_exceptions=True,  # one tool failure must not kill all others
    )

    # Log any per-tool exceptions (non-fatal)
    raw_actions = [
        ("ACT001", act001_result),
        ("ACT002", act002_result),
        ("ACT003", act003_result),
        ("ACT004", act004_result),
        ("ACT005", act005_result),
        ("ACT006", act006_result),
        ("ACT007", act007_result),
        ("ACT008", act008_result),
        ("ACT010", act010_result),
    ]
    for act_id, res in raw_actions:
        if isinstance(res, Exception):
            print(f"[postcallagent] {act_id} tool raised an exception: {res}")

    # ACT006 goes first (business rule: doubtful receivable leads the list)
    actions: list[dict] = []
    if isinstance(act006_result, dict):
        actions.append(act006_result)

    for act_id, res in raw_actions:
        if act_id == "ACT006":
            continue  # already inserted above
        if isinstance(res, dict):
            actions.append(res)

    # Assemble final output
    if isinstance(meta_result, Exception):
        print(f"[postcallagent] Meta-analysis raised an exception: {meta_result}")
        call_data["summary"] = "Error extracting summary."
        call_data["action_items"] = {"user_action": "", "agent_action_item": ""}
        call_data["categorization"] = "Unknown"
        call_data["confidence_score"] = 0.0
        call_data["confidence_reason"] = "Meta-analysis failed; confidence unavailable."
        call_data["special_notes"] = "Meta-analysis failed; special notes unavailable."
    else:
        call_data["summary"] = meta_result.summary
        # call_data["action_items"] = {
        #     "user_action": meta_result.user_action,
        #     "agent_action_item": meta_result.agent_action_item,
        # }
        call_data["categorization"] = meta_result.category
        call_data["confidence_score"] = meta_result.confidence_score
        call_data["confidence_reason"] = meta_result.confidence_reason
        call_data["special_notes"] = meta_result.special_notes
    call_data["actions"] = actions
    call_data["token_usage"] = token_cb.to_dict()

    return call_data


async def main():
    sample_call = {
        "user_name": "John Doe",
        "transcript": [
            {
                "speaker": "agent",
                "text": "Can you make payment this week?"
            },
            {
                "speaker": "customer",
                "text": "Yes, please send me another invoice copy."
            }
        ]
    }

    result = await process_transcript_and_update_json(sample_call)

    print(
        json.dumps(
            result,
            indent=4,
            default=str
        )
    )


if __name__ == "__main__":
    asyncio.run(main())