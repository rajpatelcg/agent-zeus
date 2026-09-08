import datetime
import json
from typing import Optional

from .tools.calculate_date import get_calculated_dates

try:
    from .tools.scenario_pro import format_scenario_profile_for_prompt
except Exception:
    format_scenario_profile_for_prompt = None


def get_prompt(user_data: dict, language: Optional[str] = None) -> str:
    """Follow-up collections prompt: compact, human-like, bilingual."""

    today = datetime.datetime.now().strftime("%B %d, %Y")

    collector_first_name = (
        user_data.get("collector_first_name")
        or user_data.get("collections_analyst_first_name")
        or user_data.get("agent_first_name")
        or "LISA"
    )
    collector_last_name = (
        user_data.get("collector_last_name")
        or user_data.get("collections_analyst_last_name")
        or user_data.get("agent_last_name")
        or ""
    )
    collector_full_name = f"{collector_first_name} {collector_last_name}".strip()

    customer_name = (
        user_data.get("customer_name")
        or user_data.get("user_name")
        or user_data.get("company_name")
        or user_data.get("account_name")
        or "Customer"
    )
    ap_name = (
        user_data.get("accounts_payable_name")
        or user_data.get("ap_name")
        or user_data.get("contact_name")
        or customer_name
    )
    account_number = (
        user_data.get("account_number")
        or user_data.get("account_no")
        or user_data.get("customer_account_number")
        or "Unknown"
    )
    phone = (
        user_data.get("phone_number")
        or user_data.get("user_phone")
        or user_data.get("phone")
        or "Unknown"
    )
    phone_extension = (
        user_data.get("phone_extension")
        or user_data.get("extension")
        or user_data.get("ext")
        or "Unknown"
    )
    email = (
        user_data.get("email_address")
        or user_data.get("user_email")
        or user_data.get("email")
        or "Unknown"
    )
    notes = (
        user_data.get("call_data")
        or user_data.get("notes")
        or user_data.get("call_history")
        or user_data.get("last_call_summary")
        or "No additional notes."
    )
    delay_reason = (
        user_data.get("delay_reason")
        or user_data.get("reason_for_delay")
        or user_data.get("root_cause")
        or user_data.get("rca")
        or user_data.get("customer_intent")
        or "Unknown"
    )
    effective_language = (
        language
        or user_data.get("language")
        or user_data.get("preferred_language")
        or user_data.get("locale")
        or "Unknown"
    )
    payment_terms = (
        user_data.get("payment_terms")
        or user_data.get("terms")
        or user_data.get("net_terms")
        or "Unknown"
    )
    previous_conversation_summary = (
        user_data.get("call_data")
        or user_data.get("last_call_summary")
        or user_data.get("previous_call_history")
        or "No previous conversation details available."
    )
    previous_ptp_date = (
        user_data.get("previous_ptp_date")
        or user_data.get("promise_to_pay_date")
        or user_data.get("ptp_date")
        or None
    )
    previous_ptp_amount = (
        user_data.get("previous_ptp_amount")
        or user_data.get("ptp_amount")
        or None
    )
    contact_validation_completed = bool(
        user_data.get("contact_validation_completed")
        or user_data.get("contact_validated")
        or False
    )
    contact_validation_date = (
        user_data.get("contact_validation_date")
        or user_data.get("last_contact_validation_date")
        or "Unknown"
    )

    # --- INVOICE DATA ---
    invoice_details = user_data.get("invoice_details", []) or []
    total_amount = 0.0
    invoice_numbers = []
    due_dates = []
    filtered_invoices = []

    allowed_keys = [
        "invoice_number", "invoice_no", "outstanding_balance", "due_date",
        "overdue_status", "invoice_date", "days_past_due",
        "po_number", "purchase_order_num", "dispute_status",
    ]

    if not invoice_details:
        due_dates.append(user_data.get("due_date", "Unknown date"))
        try:
            total_amount = float(user_data.get("invoice_amount", 0.0))
        except (ValueError, TypeError):
            total_amount = 0.0
        raw_inv = user_data.get("invoice_numbers") or user_data.get("invoice_number")
        if isinstance(raw_inv, list):
            invoice_numbers.extend([str(x) for x in raw_inv])
        elif raw_inv:
            invoice_numbers.append(str(raw_inv))
        filtered_invoices = [{
            "invoice_number": ", ".join(invoice_numbers) if invoice_numbers else "Unknown",
            "outstanding_balance": total_amount,
            "due_date": due_dates[0] if due_dates else "Unknown",
            "days_past_due": user_data.get("days_past_due", "Unknown"),
        }]
    else:
        for inv in invoice_details:
            if not isinstance(inv, dict):
                continue
            due_dates.append(str(inv.get("due_date", "Unknown")))
            raw_inv = inv.get("invoice_number") or inv.get("invoice_no") or inv.get("invoice") or "Unknown"
            if isinstance(raw_inv, list):
                invoice_numbers.extend([str(x) for x in raw_inv])
            else:
                invoice_numbers.append(str(raw_inv))
            try:
                total_amount += float(inv.get("outstanding_balance", 0.0))
            except (ValueError, TypeError):
                pass
            filtered_invoices.append({k: inv.get(k) for k in allowed_keys if k in inv})

    invoice_numbers = invoice_numbers or ["Unknown"]
    earliest_due = sorted(due_dates)[0] if due_dates else "Unknown"

    invoice_review_lines = []
    for i, inv in enumerate(filtered_invoices[:7], 1):
        inv_num = inv.get("invoice_number") or inv.get("invoice_no") or "Unknown"
        invoice_review_lines.append(
            f"{i}. Inv {inv_num} | ${inv.get('outstanding_balance', '?')} | "
            f"Due: {inv.get('due_date', '?')} | {inv.get('days_past_due', '?')} days past"
        )
    invoice_review_block = "\n".join(invoice_review_lines) or "No invoice details available."

    scenario_behavior = ""
    if format_scenario_profile_for_prompt:
        try:
            scenario_behavior = format_scenario_profile_for_prompt(user_data)
        except Exception:
            pass

    # --- PTP context ---
    ptp_context = ""
    if previous_ptp_date and previous_ptp_amount:
        ptp_context = f"Previous PTP: ${previous_ptp_amount} expected by {previous_ptp_date} (not yet received)."
    elif previous_ptp_date:
        ptp_context = f"Previous PTP date: {previous_ptp_date} (payment not received)."
    else:
        ptp_context = "No prior PTP on file."

    # --- Contact validation instruction ---
    contact_val_instruction = ""
    if contact_validation_completed:
        contact_val_instruction = "Contact info was recently validated — skip re-validation."
    else:
        contact_val_instruction = (
            f"Before closing, confirm: name ({ap_name}), phone ({phone} ext {phone_extension}), email ({email})."
        )

    prompt = f"""You are {collector_full_name}, a Collections Analyst at ODP Business Group. Today is {today}.

You're making a follow-up call about a past-due balance. Be conversational, professional, and concise. Ask one question at a time. Never sound robotic or scripted.

## CALL FLOW (resume from current step — never restart completed steps)

1. Greet → ask for AP contact ({ap_name}) and verify identity if yes then processed if not then politely say "I'm sorry, have a wonderful day" and end call.
2. Recording disclosure (only after verified): "This call may be recorded for quality purposes."
3. State follow-up reason and reference previous conversation
4. Review past-due invoices (up to 7, only real data)
5. Ask reason for delay
6. Detect scenario → adjust approach
7. Capture commitments (payment date/amount, action items)
8. Validate contact info if needed
9. Summarize agreed actions
10. Ask "Anything else?" → close only when done

If interrupted, answer briefly, then resume flow.

## LANGUAGE: {effective_language}

Match the customer's language. If they speak Spanish, respond in natural Spanish without announcing translation. If unknown, ask preference (English/Spanish).

## PRIVACY (before verification)

Do NOT reveal balance, invoices, or account details. If asked what the call is about:
"I'm calling from ODP Business Group regarding an administrative matter for {ap_name}. I can share details once I confirm I'm speaking with the right person."

## ACCOUNT CONTEXT

- Customer: {customer_name} | AP Contact: {ap_name}
- Account: {account_number} | Balance: ${total_amount:.2f}
- Earliest due: {earliest_due} | Terms: {payment_terms}
- {ptp_context}
- Known delay reason: {delay_reason}
- Previous conversation: {previous_conversation_summary}
- Notes: {notes}

## INVOICES

{invoice_review_block}

## SCENARIO DETECTION & TONE

Detect from customer language and adapt:
- **Payment due**: firm but respectful, slight urgency
- **Document request** (copy, statement): helpful, procedural, then pivot back to payment timing
- **Escalation** (manager, callback, settlement): calm, de-escalate, capture callback details
- **Dispute** (wrong amount, billing issue): investigative, ask what's wrong + undisputed amount
- **Partial payment** (installment, can't pay full): collaborative, get amount + date
- **Refusal/hardship** (can't pay, no money): empathetic, ask reason once, arrange follow-up

{scenario_behavior}

## ACTION CAPTURE (internal — never say codes to customer)

When customer commits, requests, or reports something, gather missing details naturally:

- **Promise to pay**: amount, date, invoices covered
- **Already paid**: reference/check/ACH#, amount, release date, invoices covered
- **Callback/escalation**: reason, preferred date/time, best contact
- **Document request**: type (invoice copy/statement/both), which invoices (specific or all past-due), delivery method, confirm email ({email})
- **Dispute**: which invoice, what's wrong, disputed vs undisputed amount
- **Missing PO**: affected invoices, PO details, expected payment date after resolution
- **Contact update**: which field, new value (note it — don't claim it's updated)
- **Refusal**: reason, whether dispute-related, escalation needed
- **Approval pending**: approver/team, expected approval date, payment release date
- **Cash flow/hardship**: earliest possible date, partial amount, priority invoices

For document requests specifically: ask type → which invoices → delivery method → confirm email.

## CONTACT VALIDATION

{contact_val_instruction}

## CLOSING

Summarize only what was actually agreed. Then: "Is there anything else I can help with?" Only close after customer confirms nothing else needed.

## RULES

- Never threaten, pressure aggressively, or invent data
- Never ask for credit card details
- Never disclose details before verification
- Never summarize commitments not explicitly made
- Use calculated dates for relative references: {get_calculated_dates()}

## RAW DATA

Invoices JSON: {json.dumps(filtered_invoices, ensure_ascii=False)}
Scenario: {user_data.get("scenario")} (score: {user_data.get("scenario_score")})
Keywords: {json.dumps(user_data.get("matched_keywords", []), ensure_ascii=False)}
Phone: {phone} | Email: {email}
"""

    return prompt