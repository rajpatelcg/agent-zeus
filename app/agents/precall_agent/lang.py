"""
LangGraph-based staged collections call agent.

Uses LangGraph's StateGraph to move the conversation through stages
as a continuous flow. Each node handles one stage; edges route
based on conversation signals. The graph never stops between stages —
it processes each user turn through the current node and conditionally
routes to the next node on the same pass.

Usage:
    from app.agents.precall_agent.call_langgraph import CollectionsCallGraph

    graph = CollectionsCallGraph(user_data=user_data, language="en")
    # On each user turn:
    response_prompt = graph.process_turn(user_message, conversation_history)
"""

import datetime
import json
from enum import Enum
from typing import Annotated, Optional, TypedDict

from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage

from .tools.calculate_date import get_calculated_dates
from .tools.scenario_profile import format_scenario_profile_for_prompt


# ═══════════════════════════════════════════════════════════════════════
# STATE DEFINITION
# ═══════════════════════════════════════════════════════════════════════

class Stage(str, Enum):
    OPENING = "opening"
    INVOICE_REVIEW = "invoice_review"
    ACTION_ITEMS = "action_items"
    CLOSING = "closing"


class CallState(TypedDict):
    """State that flows through the graph on every turn."""
    stage: str
    user_message: str
    conversation_history: list[dict]
    system_prompt: str
    fallback_needed: bool
    stage_changed: bool


# ═══════════════════════════════════════════════════════════════════════
# SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════════════

VERIFICATION_SIGNALS = [
    "yes", "speaking", "sí", "soy yo", "correct", "that's me",
    "correcto", "así es", "claro", "adelante", "dígame",
    "this is", "go ahead", "how can i help",
]

ACTION_SIGNALS = [
    "invoice", "factura", "payment", "pago", "already paid",
    "ya pagué", "sent", "check", "ach", "dispute", "disputa",
    "reason", "razón", "delay", "retraso", "need a copy",
    "necesito una copia", "promise", "will pay", "voy a pagar",
    "partial", "parcial", "hardship", "can't pay", "no puedo pagar",
]

CLOSING_SIGNALS = [
    "anything else", "algo más", "that's all", "eso es todo",
    "no more", "nada más", "goodbye", "have a good", "bye",
]

FALLBACK_SIGNALS = [
    "repeat", "again", "what was", "can you tell me", "remind me",
    "what did you say", "sorry", "didn't catch", "one more time",
    "could you repeat", "go over", "what number", "which invoice",
    "how much", "what's the balance", "what's the amount",
    "what is the total", "what account", "the invoice", "the balance",
    "repite", "otra vez", "no escuché", "cuál era", "cuánto",
    "me puede repetir", "no entendí", "qué número", "qué factura",
    "cuál es el saldo", "cuál es el monto", "perdón",
]


def _has_signal(text: str, signals: list[str]) -> bool:
    text_lower = text.lower()
    return any(s in text_lower for s in signals)


def _get_recent_user_text(history: list[dict], n: int = 4) -> str:
    user_msgs = [m.get("content", "") for m in history[-n:] if m.get("role") == "user"]
    return " ".join(user_msgs).lower()


def _get_recent_assistant_text(history: list[dict], n: int = 4) -> str:
    asst_msgs = [m.get("content", "") for m in history[-n:] if m.get("role") == "assistant"]
    return " ".join(asst_msgs).lower()


# ═══════════════════════════════════════════════════════════════════════
# GRAPH NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

class CollectionsCallGraph:
    """
    LangGraph-based collections call that flows through stages
    without pausing. Each user turn is processed; the graph decides
    whether to stay in the current stage or advance.
    """

    def __init__(self, user_data: dict, language: Optional[str] = None):
        self.user_data = user_data
        self._parse_data(language)
        self._graph = self._build_graph()
        self._current_stage = Stage.OPENING

    # ──────────────────────────────────────────────────────────────────
    # DATA PARSING (same as call_staged.py)
    # ──────────────────────────────────────────────────────────────────

    def _parse_data(self, language: Optional[str]):
        ud = self.user_data
        self.today = datetime.datetime.now().strftime("%B %d, %Y")

        self.collector_first_name = (
            ud.get("collector_first_name")
            or ud.get("collections_analyst_first_name")
            or ud.get("agent_first_name")
            or "LISA"
        )
        self.collector_last_name = (
            ud.get("collector_last_name")
            or ud.get("collections_analyst_last_name")
            or ud.get("agent_last_name")
            or ""
        )
        self.collector_full_name = f"{self.collector_first_name} {self.collector_last_name}".strip()

        self.customer_name = (
            ud.get("customer_name") or ud.get("user_name")
            or ud.get("company_name") or ud.get("account_name") or "Customer"
        )
        self.ap_name = (
            ud.get("accounts_payable_name") or ud.get("ap_name")
            or ud.get("contact_name") or self.customer_name
        )
        self.account_number = (
            ud.get("account_number") or ud.get("account_no")
            or ud.get("customer_account_number") or "Unknown"
        )
        self.phone = (
            ud.get("phone_number") or ud.get("user_phone")
            or ud.get("phone") or "Unknown"
        )
        self.email = (
            ud.get("email_address") or ud.get("user_email")
            or ud.get("email") or "Unknown"
        )
        self.notes = (
            ud.get("call_data") or ud.get("notes")
            or ud.get("call_history") or ud.get("last_call_summary")
            or "No additional notes."
        )
        self.delay_reason = (
            ud.get("delay_reason") or ud.get("reason_for_delay")
            or ud.get("root_cause") or ud.get("rca")
            or ud.get("customer_intent") or "Unknown"
        )
        self.effective_language = (
            language or ud.get("language")
            or ud.get("preferred_language") or ud.get("locale") or "Unknown"
        )

        # Invoices
        invoice_details = ud.get("invoice_details", []) or []
        self.total_amount = 0.0
        self.invoice_numbers = []
        self.due_dates = []
        self.filtered_invoices = []

        allowed_keys = [
            "invoice_number", "invoice_no", "outstanding_balance",
            "due_date", "overdue_status", "invoice_date",
            "days_past_due", "po_number", "purchase_order_num", "dispute_status",
        ]

        if not invoice_details:
            self.due_dates.append(ud.get("due_date", "Unknown date"))
            try:
                self.total_amount = float(ud.get("invoice_amount", 0.0))
            except (ValueError, TypeError):
                self.total_amount = 0.0
            raw_inv = ud.get("invoice_numbers") or ud.get("invoice_number")
            if isinstance(raw_inv, list):
                self.invoice_numbers.extend([str(x) for x in raw_inv])
            elif raw_inv:
                self.invoice_numbers.append(str(raw_inv))
            self.filtered_invoices = [{
                "invoice_number": ", ".join(self.invoice_numbers) if self.invoice_numbers else "Unknown",
                "outstanding_balance": self.total_amount,
                "due_date": self.due_dates[0] if self.due_dates else "Unknown",
            }]
        else:
            for inv in invoice_details:
                if not isinstance(inv, dict):
                    continue
                self.due_dates.append(str(inv.get("due_date", "Unknown")))
                raw_inv = inv.get("invoice_number") or inv.get("invoice_no") or inv.get("invoice") or "Unknown"
                if isinstance(raw_inv, list):
                    self.invoice_numbers.extend([str(x) for x in raw_inv])
                else:
                    self.invoice_numbers.append(str(raw_inv))
                try:
                    self.total_amount += float(inv.get("outstanding_balance", 0.0))
                except (ValueError, TypeError):
                    pass
                self.filtered_invoices.append({k: inv.get(k) for k in allowed_keys if k in inv})

        self.invoice_numbers = self.invoice_numbers or ["Unknown"]
        self.earliest_due = sorted(self.due_dates)[0] if self.due_dates else "Unknown"

        # Invoice review block
        lines = []
        for i, inv in enumerate(self.filtered_invoices[:7], start=1):
            lines.append(
                f"{i}. Invoice: {inv.get('invoice_number') or inv.get('invoice_no') or 'Unknown'}; "
                f"Balance: ${inv.get('outstanding_balance', 'Unknown')}; "
                f"Due: {inv.get('due_date', 'Unknown')}; "
                f"Days Past Due: {inv.get('days_past_due', 'Unknown')}"
            )
        self.invoice_review_block = "\n".join(lines) if lines else "No invoice details available."
        # self.scenario_behavior = format_scenario_profile_for_prompt(ud)
        self.scenario_behavior = format_scenario_profile_for_prompt(ud)
    # ──────────────────────────────────────────────────────────────────
    # GRAPH CONSTRUCTION
    # ──────────────────────────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state graph with stage nodes and conditional edges."""

        graph = StateGraph(CallState)

        # Add nodes (one per stage)
        graph.add_node("opening", self._node_opening)
        graph.add_node("invoice_review", self._node_invoice_review)
        graph.add_node("action_items", self._node_action_items)
        graph.add_node("closing", self._node_closing)

        # Set entry point
        graph.set_entry_point("opening")

        # Conditional edges: after each node runs, decide where to go next
        graph.add_conditional_edges("opening", self._route_from_opening)
        graph.add_conditional_edges("invoice_review", self._route_from_invoice_review)
        graph.add_conditional_edges("action_items", self._route_from_action_items)
        graph.add_conditional_edges("closing", self._route_from_closing)

        return graph.compile()

    # ──────────────────────────────────────────────────────────────────
    # ROUTING FUNCTIONS (edges)
    # ──────────────────────────────────────────────────────────────────

    def _route_from_opening(self, state: CallState) -> str:
        """From opening: advance to invoice_review if verified, else stay."""
        if state.get("stage_changed"):
            return END  # Prompt is ready, stop graph execution
        return END

    def _route_from_invoice_review(self, state: CallState) -> str:
        if state.get("stage_changed"):
            return END
        return END

    def _route_from_action_items(self, state: CallState) -> str:
        if state.get("stage_changed"):
            return END
        return END

    def _route_from_closing(self, state: CallState) -> str:
        return END

    # ──────────────────────────────────────────────────────────────────
    # NODE FUNCTIONS (each builds the prompt for its stage)
    # ──────────────────────────────────────────────────────────────────

    def _node_opening(self, state: CallState) -> CallState:
        """Opening stage: greeting, verification, recording disclosure."""
        history = state["conversation_history"]
        user_msg = state["user_message"]
        fallback = state.get("fallback_needed", False)

        # Check if we should advance
        recent_text = _get_recent_user_text(history)
        should_advance = _has_signal(recent_text, VERIFICATION_SIGNALS) and len(history) >= 2

        prompt = self._assemble_prompt(
            stage_instructions=self._opening_instructions(),
            fallback=fallback,
            completed_stages=[],
        )

        if should_advance:
            self._current_stage = Stage.INVOICE_REVIEW
            # Re-build with the next stage so transition is seamless
            prompt = self._assemble_prompt(
                stage_instructions=self._invoice_review_instructions(),
                fallback=fallback,
                completed_stages=["Opening & Verification"],
            )

        return {
            **state,
            "stage": self._current_stage.value,
            "system_prompt": prompt,
            "stage_changed": should_advance,
        }

    def _node_invoice_review(self, state: CallState) -> CallState:
        """Invoice review stage: walk through invoices, identify RCA."""
        history = state["conversation_history"]
        fallback = state.get("fallback_needed", False)

        recent_text = _get_recent_user_text(history)
        should_advance = _has_signal(recent_text, ACTION_SIGNALS)

        prompt = self._assemble_prompt(
            stage_instructions=self._invoice_review_instructions(),
            fallback=fallback,
            completed_stages=["Opening & Verification"],
        )

        if should_advance:
            self._current_stage = Stage.ACTION_ITEMS
            prompt = self._assemble_prompt(
                stage_instructions=self._action_items_instructions(),
                fallback=fallback,
                completed_stages=["Opening & Verification", "Invoice Review & RCA"],
            )

        return {
            **state,
            "stage": self._current_stage.value,
            "system_prompt": prompt,
            "stage_changed": should_advance,
        }

    def _node_action_items(self, state: CallState) -> CallState:
        """Action items stage: capture payments, disputes, PTP."""
        history = state["conversation_history"]
        fallback = state.get("fallback_needed", False)

        recent_text = _get_recent_user_text(history)
        assistant_text = _get_recent_assistant_text(history)
        should_advance = (
            _has_signal(assistant_text, CLOSING_SIGNALS)
            and _has_signal(recent_text, ["no", "that's all", "nothing", "nada", "eso es todo"])
        )

        prompt = self._assemble_prompt(
            stage_instructions=self._action_items_instructions(),
            fallback=fallback,
            completed_stages=["Opening & Verification", "Invoice Review & RCA"],
        )

        if should_advance:
            self._current_stage = Stage.CLOSING
            prompt = self._assemble_prompt(
                stage_instructions=self._closing_instructions(),
                fallback=fallback,
                completed_stages=["Opening & Verification", "Invoice Review & RCA", "Action Items & Payment"],
            )

        return {
            **state,
            "stage": self._current_stage.value,
            "system_prompt": prompt,
            "stage_changed": should_advance,
        }

    def _node_closing(self, state: CallState) -> CallState:
        """Closing stage: validate contact, summarize, farewell."""
        fallback = state.get("fallback_needed", False)

        prompt = self._assemble_prompt(
            stage_instructions=self._closing_instructions(),
            fallback=fallback,
            completed_stages=["Opening & Verification", "Invoice Review & RCA", "Action Items & Payment"],
        )

        return {
            **state,
            "stage": self._current_stage.value,
            "system_prompt": prompt,
            "stage_changed": False,
        }

    # ──────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────────────

    def process_turn(
        self,
        user_message: str,
        conversation_history: list[dict],
    ) -> str:
        """
        Process a user turn through the graph.
        Returns the system prompt to use for this turn's LLM call.

        The graph runs the current stage node, which:
        1. Checks if the conversation should advance
        2. Builds the appropriate prompt (current or next stage)
        3. Injects fallback data if customer asks for clarification
        """
        # Detect fallback need
        fallback_needed = _has_signal(user_message, FALLBACK_SIGNALS)

        # Build initial state
        input_state: CallState = {
            "stage": self._current_stage.value,
            "user_message": user_message,
            "conversation_history": conversation_history,
            "system_prompt": "",
            "fallback_needed": fallback_needed,
            "stage_changed": False,
        }

        # Run from the current stage node directly
        result = self._graph.invoke(input_state)

        return result["system_prompt"]

    def get_initial_prompt(self) -> str:
        """Get the initial system prompt for the start of the call."""
        return self._assemble_prompt(
            stage_instructions=self._opening_instructions(),
            fallback=False,
            completed_stages=[],
        )

    @property
    def current_stage(self) -> str:
        return self._current_stage.value

    # ──────────────────────────────────────────────────────────────────
    # PROMPT ASSEMBLY
    # ──────────────────────────────────────────────────────────────────

    def _assemble_prompt(
        self,
        stage_instructions: str,
        fallback: bool,
        completed_stages: list[str],
    ) -> str:
        """
        Assemble the final prompt from parts.
        Only includes: role + language + style + current stage + context.
        If fallback triggered, appends full data reference.
        """
        parts = [
            self._role_header(),
            self._language_rules(),
            self._style_rules(),
        ]

        if completed_stages:
            parts.append(self._completed_reminder(completed_stages))

        parts.append(stage_instructions)
        parts.append(self._context_reference())

        if fallback:
            parts.append(self._fallback_reference())

        return "\n\n".join(parts)

    # ──────────────────────────────────────────────────────────────────
    # PROMPT SECTIONS
    # ──────────────────────────────────────────────────────────────────

    def _role_header(self) -> str:
        return f"""# Role
You are {self.collector_full_name}, a warm, professional Collections Agent from ODP Business Group / Business Solutions.
You are having a natural business conversation, not reading a script."""

    def _language_rules(self) -> str:
        return f"""# Language
Effective language: {self.effective_language}
- Speak in the customer's language.
- If customer speaks Spanish, continue naturally in Spanish. Do not say you are translating.
- If language is unknown, ask whether English or Spanish is preferred.
Spanish yes/verified: sí, si, claro, correcto, así es, soy yo, dígame, adelante.
Spanish wrong-person: no, número equivocado, persona equivocada, no soy esa persona, se equivocó."""

    def _style_rules(self) -> str:
        return """# Style
- Short responses. One question at a time. Acknowledge before asking.
- Calm, respectful, helpful, slightly urgent. Never threaten.
- Never ask for credit card details. Never invent data.
Useful: "Got it." / "I understand." / "Thanks for clarifying." / "Let me make sure I'm noting that correctly."
"""

    def _completed_reminder(self, stages: list[str]) -> str:
        lines = [f"- {s}: COMPLETED" for s in stages]
        return "# Previous Steps Completed\n" + "\n".join(lines)

    def _context_reference(self) -> str:
        return f"""# Context Reference (always available)
Today: {self.today}
Collector: {self.collector_full_name}
AP Contact: {self.ap_name}
Account Number: {self.account_number}
Past Due Balance: ${self.total_amount:.2f}
Earliest Due Date: {self.earliest_due}
Invoice Numbers: {", ".join(self.invoice_numbers)}
Phone: {self.phone}
Email: {self.email}
Language: {self.effective_language}
Scenario: {self.user_data.get("scenario")}
Notes: {self.notes}

## Invoice Summary
{self.invoice_review_block}"""

    def _fallback_reference(self) -> str:
        return f"""# Fallback Reference (customer asked for clarification)

IMPORTANT: The customer is asking you to repeat or clarify something.
Use ALL the data below to answer. Do NOT say you cannot access it.

## Account & Contact
- Collector: {self.collector_full_name}
- AP Contact: {self.ap_name}
- Account Number: {self.account_number}
- Phone: {self.phone}
- Email: {self.email}

## Balance & Invoices
- Total Past Due: ${self.total_amount:.2f}
- Earliest Due Date: {self.earliest_due}
- Invoice Numbers: {", ".join(self.invoice_numbers)}

## Detailed Invoice Data
{self.invoice_review_block}

## Full JSON
{json.dumps(self.filtered_invoices, ensure_ascii=False)}

## Calculated Dates
{get_calculated_dates()}

## Notes / History
{self.notes}"""

    # ──────────────────────────────────────────────────────────────────
    # STAGE INSTRUCTIONS
    # ──────────────────────────────────────────────────────────────────

    def _opening_instructions(self) -> str:
        return f"""# Current Stage: Opening & Verification

Goals:
1. Greet and ask for the AP contact.
2. Verify identity before sharing any account details.
3. Deliver recording disclosure after verification.

## Privacy (before verification)
Do NOT disclose: balance, invoice numbers, due dates, debt details.
If asked what the call is about:
"I'm calling from ODP Business Group regarding an administrative matter for {self.ap_name}. Once I confirm I'm speaking with the right person, I can share the details."

## Opening
English: "Hi, good day, this is {self.collector_full_name}, calling from ODP Business Group. I'm looking for {self.ap_name}, please."
Spanish: "Hola, buen día. Le habla {self.collector_full_name} de ODP Business Group. ¿Podría comunicarme con {self.ap_name}, por favor?"

## Verify
"Am I speaking with {self.ap_name}?" / "¿Estoy hablando con {self.ap_name}?"

## After Verified
"Please be advised that this call may be recorded for training and quality purposes."
Spanish: "Le informo que esta llamada puede ser grabada con propósitos de entrenamiento y calidad."

## Then state reason:
"I'm following up on the past due balance of ${self.total_amount:.2f} on account number {self.account_number}. We noticed payment has not been received yet, so I wanted to check whether it has already been released."
Then: "Would it be okay if I give you the invoice numbers so we can review the status?"

## Scenario Guidance
{self.scenario_behavior}"""

    def _invoice_review_instructions(self) -> str:
        return f"""# Current Stage: Invoice Review & Reason for Delay

Goals:
1. Walk through past due invoices.
2. Identify the root cause / delay reason.
3. Adapt tone based on the reason given.

## Invoice Review
Review up to 7 invoices. Use only provided data.
"The first invoice still showing past due is [Invoice Number], with a balance of [Amount], due on [Due Date]."
Then ask: "What was the main reason for the delay?" / "¿Cuál ha sido la principal razón del retraso?"

## Invoices
{self.invoice_review_block}

## Full Invoice Data
{json.dumps(self.filtered_invoices, ensure_ascii=False)}

## RCA Tone
- Invoice/statement needed → helpful; confirm doc type & payment timing.
- Dispute → calm; ask disputed invoice, issue, undisputed amount.
- Approval pending → practical; ask approver, date, payment date.
- Missing PO → solution-focused; ask invoices, PO detail, timing.
- Already paid → positive; ask reference, amount, date, invoices covered.
- Cash flow/hardship → empathetic; ask earliest date, partial amount.
- Refusal → calm; ask reason once, offer senior follow-up.
- Callback → respectful; capture reason, date, time, contact.

Known delay reason: {self.delay_reason}

## Scenario Guidance
{self.scenario_behavior}

## Calculated Dates
{get_calculated_dates()}"""

    def _action_items_instructions(self) -> str:
        return f"""# Current Stage: Action Items & Payment Details

Goals:
1. Detect action items from customer statements.
2. Ask only missing required fields, one at a time.
3. Collect payment details (PTP or already paid).
4. Do NOT assume missing details.

## Action Item Detection
When the customer requests, promises, disputes, or reports something — detect it and ask only what's missing. Never say action codes to the customer.

## Action Types & Required Fields
- ACT001 PromiseToPay: amount, payment date, invoices covered.
- ACT002 PaymentAlreadySent: reference/check/ACH#, amount, release date, invoices.
- ACT003 ExecutiveCallback: reason, callback date/time, best contact.
- ACT004 DocumentCopy: doc type, invoice numbers, delivery channel, confirmed email.
  If "send me a copy": "Do you need invoice copies, a statement, or both?"
  → "Which invoices?" → "By email?" → "Is {self.email} still the best email?"
- ACT005 BillingDispute: disputed invoice, reason, disputed/undisputed amount.
- ACT006 MissingPO: affected invoices, PO detail, expected payment date.
- ACT007 ContactUpdate: field to update, new value, confirming person. (Say you'll note it.)
- ACT008 RefusalOrNoPromise: reason, dispute or not, escalation needed.
- ACT009 ApprovalPending: approver/team, approval date, payment release date, invoices.
- ACT010 CashFlowOrHardship: earliest payment date, partial amount, priority invoices.

## Payment Follow-Up
Already paid → collect: reference#, amount, release date, invoices covered.
Not paid → collect: expected date, amount, invoices covered.
Relative date → use calculated dates, confirm: "So that would be [Exact Date], correct?"

## Existing Action Items
{json.dumps(self.user_data.get("action_items", []), ensure_ascii=False)}

## Calculated Dates
{get_calculated_dates()}"""

    def _closing_instructions(self) -> str:
        return f"""# Current Stage: Closing

Goals:
1. Validate contact information.
2. Summarize all captured action items.
3. Close professionally.

## Contact Validation
- "Is your name listed correctly as {self.ap_name}?"
- "Is {self.phone} still the best phone number?"
- "Is {self.email} still the best email for statements or payment notifications?"
If corrected: "Thank you, I'll note that for follow-up."

## Summary
Before closing, summarize everything captured:
- PTP / payment sent / document request / dispute / callback
- Missing PO / approval pending / contact update / hardship
- Any missing details still needed

Then: "Is there anything else I can help you with today?"

## Final Close
Only after customer says no:
"Thank you for your time today. Have a wonderful day."
"""


# ═══════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS (drop-in compatible with caller.py)
# ═══════════════════════════════════════════════════════════════════════

def create_collections_graph(user_data: dict, language: Optional[str] = None) -> CollectionsCallGraph:
    """Create a new graph instance for a call."""
    return CollectionsCallGraph(user_data=user_data, language=language)


def get_prompt(user_data: dict, language: Optional[str] = None) -> str:
    """Backward-compatible: returns the opening prompt (no graph state)."""
    graph = CollectionsCallGraph(user_data=user_data, language=language)
    return graph.get_initial_prompt()
