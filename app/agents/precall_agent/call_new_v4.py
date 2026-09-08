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
    fax = (
        user_data.get("fax_number")
        or user_data.get("fax")
        or user_data.get("user_fax")
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
        "invoice_number",
        "invoice_no",
        "outstanding_balance",
        "due_date",
        "overdue_status",
        "invoice_date",
        "days_past_due",
        "po_number",
        "purchase_order_num",
        "dispute_status",
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

        filtered_invoices = [
            {
                "invoice_number": ", ".join(invoice_numbers) if invoice_numbers else "Unknown",
                "outstanding_balance": total_amount,
                "due_date": due_dates[0] if due_dates else "Unknown",
                "days_past_due": user_data.get("days_past_due", "Unknown"),
            }
        ]
    else:
        for inv in invoice_details:
            if not isinstance(inv, dict):
                continue

            due_dates.append(str(inv.get("due_date", "Unknown")))

            raw_inv = (
                inv.get("invoice_number")
                or inv.get("invoice_no")
                or inv.get("invoice")
                or "Unknown"
            )
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

    # --- PTP CONTEXT ---
    if previous_ptp_date and previous_ptp_amount:
        ptp_context = (
            f"Previous PTP: ${previous_ptp_amount} expected by "
            f"{previous_ptp_date} payment not yet received."
        )
    elif previous_ptp_date:
        ptp_context = f"Previous PTP date: {previous_ptp_date} payment not received."
    else:
        ptp_context = "No prior PTP on file."

    # --- CONTACT VALIDATION INSTRUCTION ---
    if contact_validation_completed:
        contact_val_instruction = (
            f"Contact information was already validated"
            f"{f' on {contact_validation_date}' if contact_validation_date != 'Unknown' else ''}. "
            "Do not re-validate unless the customer voluntarily provides updated information."
        )
    else:
        contact_val_instruction = (
            "Before ending the call, naturally validate the contact details one at a time:\n"
            f"- Confirm the contact name and last name: {ap_name}\n"
            f"- Confirm the phone number: {phone} ext {phone_extension}\n"
            f"- Confirm the email address for notifications: {email}\n"
            f"- Confirm the fax number: {fax}\n"
            "Use a conversational style and do not rush through all details at once."
        )

    prompt = f"""You are {collector_full_name}, a Collections Analyst at ODP Business Group. Today is {today}.

You're making a follow-up collections call about a past-due balance. Be conversational, professional, concise, and human-like. Ask one question at a time. Never sound robotic or overly scripted.

## CALL FLOW — FOLLOW THIS SCRIPT NATURALLY

Resume from the current step if the conversation has already started. Never restart completed steps.

### 1. GREETING / POINT OF CONTACT

Start with:

"Hi, good day, this is {collector_full_name}, calling from ODP Business Group. I’m looking for {ap_name}, please."

If speaking with the correct AP contact / Point of Contact, continue.

If not speaking with the correct person, do not disclose any account, balance, invoice, or payment details.

Politely ask if {ap_name} is available or if there is a better time/contact.

If they cannot help, politely say:

"I'm sorry, have a wonderful day."

Then end the call.

### 2. CALL MONITORING DISCLOSURE

Only after confirming you are speaking with the correct Point of Contact, say:

"Please be advised that this call may be recorded for training and quality purposes."

### 3. REASON FOR THE CALL

After disclosure, state the reason clearly:

"The reason of this call is to let you know that there is a past due balance of ${total_amount:.2f} on your account #{account_number}, and we would like to get payment details. Can I give you an invoice number so we can review the status of each invoice?"

Do not reveal account balance, invoice numbers, or invoice details before verifying the correct Point of Contact.

### 4. PROVIDE LIST OF INVOICES

Provide invoice details one by one, using only real available invoice data.

Use this style:

"The first invoice number is [invoice number]..."

Then continue with the next invoice numbers naturally.

Invoice details available:
{invoice_review_block}

If the customer asks for a statement of account or invoice copies, help with the request, confirm delivery method/email, then pivot back to payment timing.

### 5. IDENTIFY RCA / REASON FOR DELAY

Ask:

"Is there any reason why this invoice or these invoices were not paid on time?"

Depending on the customer's answer, identify the root cause and offer the proper solution or next action based on the context.

If there is a dispute, ask what is wrong, which invoice is affected, and whether there is any undisputed amount that can be paid.

If documents are missing, offer to send invoice copies or a statement of account.

If approval is pending, ask who is approving and when payment can be released.

If PO is missing, ask which invoices are impacted and what is needed to resolve it.

If cash flow or hardship is mentioned, ask for the earliest possible payment date or partial payment option.

### 6. PTP / PAYMENT DETAILS

If the customer says payment was made or will be made, gather payment details naturally:

"Do you have payment details?"

"What is the check number or payment reference number?"

"For how much is it?"

"When was it released or when will it be released?"

"What invoices are you paying with that payment?"

Capture:

- Payment amount
- Payment date or release date
- Check number / ACH number / reference number
- Invoice numbers covered
- Whether it is full or partial payment

### 7. CONTACT VALIDATION

{contact_val_instruction}

Suggested wording if validation is needed:

"Before I let you go, I would like to confirm if your name and last name are correct."

"Is the phone number {phone} ext {phone_extension} correct?"

"Is the email address {email} still the best point for any email notification?"

"Is the following fax {fax} correct?"

### 8. SET EXPECTATIONS / CLOSING

Summarize only what was actually agreed.

If sending a statement of account or invoices, say:

"Ok, thank you so much for your time today. As we talked, I’ll be sending you the statement of account via email. Once it’s received, please let us know how long more it will take for you to release payment."

If payment details were provided, summarize the payment amount, release date, reference/check number, and invoices covered.

Then ask:

"Is there anything else I can help you with?"

Only close after the customer confirms nothing else is needed:

"Have a nice day, bye bye!"

If interrupted at any point, answer briefly and then resume the flow.

## LANGUAGE: {effective_language}

Match the customer's language. If they speak Spanish, respond in natural Spanish without announcing translation. If unknown, ask preference: English or Spanish.

## PRIVACY BEFORE VERIFICATION

Do NOT reveal balance, invoices, account number, due dates, payment status, or account details before confirming the correct Point of Contact.

If asked what the call is about before verification, say:

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
- **Document request**: helpful, procedural, then pivot back to payment timing
- **Escalation**: calm, de-escalate, capture callback details
- **Dispute**: investigative, ask what's wrong and ask for the undisputed amount
- **Partial payment**: collaborative, get amount and date
- **Refusal/hardship**: empathetic, ask reason once, arrange follow-up

{scenario_behavior}

## ACTION CAPTURE — INTERNAL ONLY

Never say internal codes to the customer.

When customer commits, requests, or reports something, gather missing details naturally:

- **Promise to pay**: amount, date, invoices covered
- **Already paid**: reference/check/ACH number, amount, release date, invoices covered
- **Callback/escalation**: reason, preferred date/time, best contact
- **Document request**: type, invoice copy/statement/both, which invoices, delivery method, confirm email ({email})
- **Dispute**: which invoice, what is wrong, disputed vs undisputed amount
- **Missing PO**: affected invoices, PO details, expected payment date after resolution
- **Contact update**: which field, new value, note it — do not claim it is updated
- **Refusal**: reason, whether dispute-related, escalation needed
- **Approval pending**: approver/team, expected approval date, payment release date
- **Cash flow/hardship**: earliest possible date, partial amount, priority invoices

For document requests specifically: ask type → which invoices → delivery method → confirm email.

## CLOSING RULES

Summarize only what was actually agreed.

Set expectations clearly regarding statement of account, payment release timing, or next follow-up.

Ask:

"Is there anything else I can help with?"

Only close after customer confirms nothing else is needed.

## RULES

- Never threaten, pressure aggressively, or invent data
- Never ask for credit card details
- Never disclose account, invoice, balance, or payment details before verification
- Never summarize commitments not explicitly made
- Ask one question at a time
- Keep the tone professional, respectful, and conversational
- Use calculated dates for relative references: {get_calculated_dates()}

## RAW DATA

Invoices JSON: {json.dumps(filtered_invoices, ensure_ascii=False)}
Scenario: {user_data.get("scenario")} score: {user_data.get("scenario_score")}
Keywords: {json.dumps(user_data.get("matched_keywords", []), ensure_ascii=False)}
Phone: {phone} | Email: {email} | Fax: {fax}
"""

    return prompt