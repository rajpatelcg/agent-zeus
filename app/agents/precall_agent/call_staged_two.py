"""Compact staged collections agent (call_staged_two)

Self-contained, token-efficient staged agent:
- Inlines `CallStage`, `ConversationState`, and `StagedPromptBuilder` so
  it does not depend on importing the full langchain file.
- Uses a lightweight LLM stage detector with a sequential fallback.
- Defers contact validation until the end of the flow unless it was
  already completed earlier in the call.
"""

from typing import Any, Dict, List
import datetime
from enum import Enum

try:
    from .tools.scenario_profile import format_scenario_profile_for_prompt
except Exception:  # pragma: no cover - optional dependency / direct execution
    format_scenario_profile_for_prompt = None


class CallStage(Enum):
    GREETING = "greeting"
    MONITORING_DISCLOSURE = "monitoring_disclosure"
    REASON_FOR_CALL = "reason_for_call"
    INVOICE_REVIEW = "invoice_review"
    RCA_IDENTIFICATION = "rca_identification"
    PAYMENT_GATHERING = "payment_gathering"
    CONTACT_VALIDATION = "contact_validation"
    CLOSING = "closing"
    COMPLETED = "completed"


class ConversationState:
    def __init__(self):
        self.current_stage = CallStage.GREETING
        self.completed_stages = set()
        self.collected_info: Dict[str, Any] = {}
        self.verified_poc = False
        self.monitoring_disclosed = False
        self.invoices_discussed: List[str] = []
        self.rca_identified = False
        self.payment_details_gathered = False
        self.contacts_validated = {
            "name": False,
            "phone": False,
            "email": False,
            "fax": False,
        }
        self.language_detected = None
        self.conversation_history: List[Dict[str, str]] = []

    def mark_stage_complete(self, stage: CallStage):
        self.completed_stages.add(stage)

    def is_stage_complete(self, stage: CallStage) -> bool:
        return stage in self.completed_stages

    def update_collected_info(self, key: str, value: Any):
        self.collected_info[key] = value

    def has_info(self, key: str) -> bool:
        return key in self.collected_info and bool(self.collected_info[key])


class StagedPromptBuilder:
    """Minimal prompt builder adapted from call_staged_langchain.

    Only contains the methods the compact agent uses: base instructions and
    per-stage prompts plus invoice/contact extraction helpers.
    """

    def __init__(self, user_data: dict):
        self.user_data = user_data
        self.today = datetime.datetime.now().strftime("%B %d, %Y")
        self.collector_name = self._get_collector_name()
        self.customer_name = self._get_customer_name()
        self.ap_name = self._get_ap_name()
        self.account_number = self._get_account_number()
        self.total_amount = self._calculate_total_amount()
        self.invoices = self._prepare_invoices()
        self.contact_info = self._get_contact_info()
        self.language = self._get_language()
        self.scenario_behavior = self._get_scenario_behavior()

    def _get_collector_name(self) -> str:
        first = self.user_data.get("collector_first_name", "LISA")
        last = self.user_data.get("collector_last_name", "")
        return f"{first} {last}".strip()

    def _get_customer_name(self) -> str:
        return self.user_data.get("customer_name", "Customer")

    def _get_ap_name(self) -> str:
        return self.user_data.get("accounts_payable_name", self._get_customer_name())

    def _get_account_number(self) -> str:
        return self.user_data.get("account_number", "Unknown")

    def _calculate_total_amount(self) -> float:
        invoice_details = self.user_data.get("invoice_details", [])
        if not invoice_details:
            try:
                return float(self.user_data.get("invoice_amount", 0.0))
            except (ValueError, TypeError):
                return 0.0
        total = 0.0
        for inv in invoice_details:
            if isinstance(inv, dict):
                try:
                    total += float(inv.get("outstanding_balance", 0.0))
                except (ValueError, TypeError):
                    pass
        return total

    def _prepare_invoices(self) -> List[Dict[str, Any]]:
        invoice_details = self.user_data.get("invoice_details", [])
        if not invoice_details:
            return [{
                "invoice_number": self.user_data.get("invoice_number", "Unknown"),
                "outstanding_balance": self.total_amount,
                "due_date": self.user_data.get("due_date", "Unknown"),
                "days_past_due": self.user_data.get("days_past_due", "Unknown"),
            }]
        prepared = []
        for inv in invoice_details[:7]:
            if isinstance(inv, dict):
                prepared.append({
                    "invoice_number": inv.get("invoice_number", "Unknown"),
                    "outstanding_balance": inv.get("outstanding_balance", 0),
                    "due_date": inv.get("due_date", "Unknown"),
                    "days_past_due": inv.get("days_past_due", "Unknown"),
                })
        return prepared

    def _get_contact_info(self) -> Dict[str, Any]:
        return {
            "phone": self.user_data.get("phone_number", "Unknown"),
            "extension": self.user_data.get("phone_extension", "Unknown"),
            "email": self.user_data.get("email_address", "Unknown"),
            "fax": self.user_data.get("fax_number", "Unknown"),
            "validated": bool(self.user_data.get("contact_validation_completed", False)),
            "validation_date": self.user_data.get("contact_validation_date", "Unknown"),
        }

    def _get_language(self) -> str:
        return self.user_data.get("language", "English")

    def _get_scenario_behavior(self) -> str:
        """Scenario-based tone/behavior profile derived from user_data."""
        if format_scenario_profile_for_prompt is None:
            return ""
        try:
            return format_scenario_profile_for_prompt(self.user_data) or ""
        except Exception:
            return ""

    def _get_payment_terms(self) -> str:
        return str(self.user_data.get("payment_terms") or self.user_data.get("terms") or "NET Unknown")

    def _get_avg_days_past_due(self):
        vals = []
        for inv in self.invoices:
            try:
                vals.append(int(inv.get("days_past_due")))
            except (ValueError, TypeError):
                pass
        return round(sum(vals) / len(vals)) if vals else "several"

    SCENARIO_SELF_DETECT = (
        "SCENARIO (self-detect): No scenario profile was pre-detected. Infer the customer's situation "
        "from their responses — payment due, invoice/document request, escalation, billing dispute, "
        "partial payment, or refusal/cannot pay — and adapt your tone accordingly: firm-but-respectful "
        "for past due, helpful for document requests, calm de-escalating for escalations, patient and "
        "non-defensive for disputes, collaborative for partial payment, empathetic and low-pressure for "
        "refusal/hardship. Re-read the situation each turn and shift tone smartly if it changes."
    )

    def get_base_instructions(self) -> str:
        scenario_block = (
            f"\n\n{self.scenario_behavior}"
            if self.scenario_behavior
            else f"\n\n{self.SCENARIO_SELF_DETECT}"
        )
        return f"""You are {self.collector_name}, a Collections Analyst at ODP Business Group. Today is {self.today}.

BILINGUAL FLUENCY: You speak both English and Spanish fluently (including Caribbean/Puerto Rican Spanish).

LANGUAGE RULES:
- If customer speaks Spanish, respond in natural Spanish immediately (no announcement)
- If customer speaks English, respond in English
- If unknown, start English and switch if they respond in Spanish
- Match their language style naturally

KEY BEHAVIORS:
- Be conversational, professional, and human-like
- Ask ONE question at a time
- Never sound robotic or scripted
- Listen to customer responses before proceeding
- NEVER repeat information already shared
- Adapt based on what customer says

SOUND HUMAN, NOT SCRIPTED:
- The EN/ES lines in each stage are REFERENCE for meaning and tone only. Never read them verbatim — rephrase in your own natural words each time so no two calls sound identical.
- Use contractions (I'm, we'd, that's, you're) and everyday phrasing. Avoid stiff, formal wording like "Please be advised".
- Briefly acknowledge what the customer just said before moving on ("Okay, thanks for confirming", "Got it", "I appreciate that", "Sure, one moment").
- Use light, natural connectors and the occasional filler where a person would ("Alright", "So", "Let me see here", "Bear with me a sec").
- React with genuine empathy when the customer explains a problem, delay, or hardship — don't jump straight to the next task.
- Vary sentence length and openings; don't start every turn the same way.
- Keep it warm and professional, like a real person on the phone — not a recording.

PACING (match a real phone conversation):
- Adapt your pace to the moment: move at a normal, easy pace for casual chat, and slow down noticeably for anything the customer must write down or confirm.
- While verifying contact details like reading an EMAIL ADDRESS, PHONE NUMBER, EXTENSION, FAX, or ACCOUNT/INVOICE NUMBER, slow right down — say it in small chunks, pause between groups of digits, and offer to spell it out or repeat it.
- Speak DATES the natural way a person would say them out loud (e.g. "January fifteenth" not "January 15, 2026", "the fifteenth of this month", "next Tuesday") — never read a raw date string.
- Speak DOLLAR AMOUNTS naturally too (e.g. "fifteen hundred dollars" or "one thousand five hundred"), not "$1500.00".
- After giving important details, give the customer a beat to catch up before moving on.

- Use the scenario profile below only as tone guidance; let the customer's actual words lead, and shift your tone smartly if the situation changes mid-call{scenario_block}"""

    def get_stage_prompt(self, stage: CallStage, state: ConversationState) -> str:
        stage_methods = {
            CallStage.GREETING: self._prompt_greeting,
            CallStage.MONITORING_DISCLOSURE: self._prompt_monitoring,
            CallStage.REASON_FOR_CALL: self._prompt_reason,
            CallStage.INVOICE_REVIEW: self._prompt_invoice_review,
            CallStage.RCA_IDENTIFICATION: self._prompt_rca,
            CallStage.PAYMENT_GATHERING: self._prompt_payment,
            CallStage.CONTACT_VALIDATION: self._prompt_contact_validation,
            CallStage.CLOSING: self._prompt_closing,
        }
        method = stage_methods.get(stage)
        if method:
            return method(state)
        return ""

    def _prompt_greeting(self, state: ConversationState) -> str:
        return f"""CURRENT TASK: Greeting & Point of Contact Verification

YOU ARE CALLING: {self.ap_name} at {self.customer_name}

ACTION:
English: "Hi, good day, this is {self.collector_name}, calling from ODP Business Group. I'm looking for {self.ap_name}, please."
Spanish: "Hola, buen día, le habla {self.collector_name}, llamando de ODP Business Group. Busco a {self.ap_name}, por favor."

WAIT for customer response.

IF correct person: Proceed naturally to next stage
IF NOT correct person: Ask when they're available or for alternate contact, then politely end call
IF asked what call is about: "I'm calling from ODP Business Group regarding an administrative matter. I can share details once I confirm I'm speaking with the right person."

DO NOT reveal: account balance, invoices, due dates, or any account details until POC confirmed."""

    def _prompt_monitoring(self, state: ConversationState) -> str:
        if state.monitoring_disclosed:
            return "SKIP: Monitoring disclosure already completed."

        return f"""CURRENT TASK: Call Monitoring Disclosure

ONLY after confirming you're speaking with {self.ap_name}. Keep it light and offhand — a quick heads-up, not a legal announcement. Paraphrase naturally; do not say "Please be advised".

English (paraphrase naturally): "Just so you know, this call may be recorded for quality and training."
Spanish (paraphrase naturally): "Solo para que sepa, esta llamada puede ser grabada con fines de calidad y entrenamiento."

WAIT for acknowledgment, then proceed naturally."""

    def _prompt_reason(self, state: ConversationState) -> str:
        return f"""CURRENT TASK: State Reason for Call (follow-up + sense of urgency)

English: "The reason for this call is to follow up on a previous conversation about the past due balance of ${self.total_amount:.2f} on your account #{self.account_number}. We noticed that no payment has been made yet, so we'd like to find out if it was already released, please."

Spanish: "El motivo de esta llamada es dar seguimiento a una conversación previa sobre el balance vencido de ${self.total_amount:.2f} en su cuenta #{self.account_number}. Notamos que aún no se ha realizado ningún pago, por lo que nos gustaría saber si ya fue liberado, por favor."

WAIT for customer response. If they ask questions, answer naturally before proceeding."""

    def _prompt_invoice_review(self, state: ConversationState) -> str:
        discussed = state.invoices_discussed
        remaining = [inv for inv in self.invoices if inv["invoice_number"] not in discussed]

        if not remaining:
            return "All invoices discussed. Proceed to next stage."

        next_invoice = remaining[0]
        invoice_num = next_invoice["invoice_number"]
        balance = next_invoice["outstanding_balance"]
        due_date = next_invoice["due_date"]
        days_past = next_invoice["days_past_due"]

        position = len(discussed) + 1
        total_invoices = len(self.invoices)
        ordinal = "first" if position == 1 else "next"
        ordinal_es = "primera" if position == 1 else "siguiente"
        ack_instruction = (
            "Proceed to next invoice"
            if len(remaining) > 1
            else 'All invoices shared — ask why they weren\'t paid on time (e.g. "Is there any reason these weren\'t paid on time?") and note the delay reason before moving to RCA'
        )

        return f"""CURRENT TASK: Share Invoice Details ({position} of {total_invoices})

English: "The {ordinal} invoice number still past due is {invoice_num}, for ${balance}, due {due_date}, {days_past} days past due."
Spanish: "La {ordinal_es} factura que sigue vencida es la número {invoice_num}, por ${balance}, con vencimiento {due_date}, con {days_past} días de atraso."

WAIT for customer response or acknowledgment.

IF customer asks about this invoice (dispute, already paid, missing PO, etc.): Handle naturally
IF customer acknowledges: {ack_instruction}
IF customer requests statement/copies: Offer to send, confirm email, then return to payment timing

FALLBACK: If customer already mentioned payment/dispute for this invoice, acknowledge and move on without repeating."""

    def _prompt_rca(self, state: ConversationState) -> str:
        if state.rca_identified:
            return "RCA already identified. Proceed based on identified reason."

        previous_rca = self.user_data.get("delay_reason", "")
        rca_hint = f"\n\nNOTE: Previous delay reason on file: {previous_rca}" if previous_rca and previous_rca != "Unknown" else ""
        avg_days = self._get_avg_days_past_due()
        terms = self._get_payment_terms()

        return f"""CURRENT TASK: Identify Reason for Delay (show sense of urgency)

English: "What was the main reason causing the delay? We're truly concerned because on average these invoices are more than {avg_days} days past due and your terms are {terms}."
Spanish: "¿Cuál fue la razón principal del retraso? Estamos realmente preocupados porque en promedio estas facturas tienen más de {avg_days} días de atraso y sus términos son {terms}."

WAIT and LISTEN to customer response, then CAPTURE the delay reason. Always ask this — never skip it — and make sure you note the reason the customer gives.{rca_hint}

Once they explain, briefly repeat the reason back to confirm you've got it right (e.g. "Okay, so it's held up in approval — got it") before moving on. This confirms the delay reason is recorded.

PAYMENT DATE: When payment is still outstanding, ask: "What date can you realistically make the payment?" Accept only a date from tomorrow through 30 days from today. If the customer suggests an earlier or later date, explain that you need the earliest realistic date within that window and ask again. Do not invent a date if the customer cannot commit to one.

BASED ON RESPONSE, detect the matching action item and ask ONLY its important missing details (never re-ask what was already answered), offering a solution or taking the proper action per DTP with urgency:
- ACT001 PromiseToPay — customer promises to pay the FULL balance: need amount, date, invoices covered
- ACT002 Escalation — wants a callback from a senior/manager/supervisor: need reason, escalation level, role, callback date/time
- ACT003 Dispute — contests a charge or undelivered product/service: need disputed invoice/order, dispute reason, disputed amount
- ACT004 DocumentCopy — requests an invoice copy/credit memo/statement: need document type, invoice numbers/all, delivery channel, confirm email/address
- ACT005 PartialPayment — commits to pay LESS than the full balance: need amount, date, payment method
- ACT006 DoubtfulPayment — refuses/cannot pay or is materially uncertain: need non-payment reason (NPR), dispute or not, escalation needed
- ACT007 CreditRequest — asks for a discount/credit or makes payment conditional on one: need requested amount, reason, requested date
- ACT008 OtherCustomerRequest — any request NOT covered above: need request details, preferred time
- ACT010 StatementOfAccount — requests a Statement of Account (SOA)/invoice copy/credit memo be sent: need document type, invoice numbers/all, delivery channel, confirm email/address

ADAPT naturally based on their specific situation."""

    def _prompt_payment(self, state: ConversationState) -> str:
        if state.payment_details_gathered:
            return "Payment details already collected."

        rca = state.collected_info.get("rca", "").lower()

        if "already paid" in rca or "payment" in rca:
            already_paid = "already paid" in rca
            reference_line = (
                '1. Payment reference/check/ACH number\nEnglish: "What is the check number or payment reference?"\nSpanish: "¿Cuál es el número de cheque o referencia del pago?"\n\n'
                if already_paid
                else ""
            )
            return f"""CURRENT TASK: Promise to Pay / Gather Payment Details

If no previous PTP was provided, ask for the proper details. Ask naturally ONE question at a time.
Only ask for the check/reference number if the payment was already sent — skip it for a future promise-to-pay.

Opener
English: "By any chance, do you have the payment details already?"
Spanish: "¿Por casualidad ya tiene los detalles del pago?"

{reference_line}Payment amount
English: "For how much is the payment?"
Spanish: "¿Por cuánto es el pago?"

Payment date/release date
English: "When was it released or when will it be released?"
Spanish: "¿Cuándo fue liberado o cuándo será liberado?"

Invoices covered
English: "Which invoices are you paying with?"
Spanish: "¿Qué facturas está pagando?"

FALLBACK: If customer already provided any detail, skip that question."""

        return "No payment to gather. Proceed based on customer situation."

    def _prompt_contact_validation(self, state: ConversationState) -> str:
        if self.contact_info["validated"]:
            validation_date = self.contact_info["validation_date"]
            validated_on = f" on {validation_date}" if validation_date != "Unknown" else ""
            return f"""Contact information was already validated{validated_on} (Call 1 or within the last 6 months).

DISREGARD this step. Do NOT re-validate unless the customer voluntarily provides updates.
Add a Webcollect note that no validation was completed since it was done on {validation_date}.
Proceed to closing."""

        remaining = [k for k, v in state.contacts_validated.items() if not v]

        if not remaining:
            return "All contacts validated. Proceed to closing."

        next_field = remaining[0]

        validation_prompts = {
            "name": {
                "en": f"Before we finish, I'd like to confirm your name is {self.ap_name} - is that correct?",
                "es": f"Antes de terminar, me gustaría confirmar que su nombre es {self.ap_name} - ¿es correcto?",
            },
            "phone": {
                "en": f"And is {self.contact_info['phone']} extension {self.contact_info['extension']} still the best phone number to reach you?",
                "es": f"¿Y el número {self.contact_info['phone']} extensión {self.contact_info['extension']} sigue siendo el mejor teléfono para contactarle?",
            },
            "email": {
                "en": f"For email notifications, is {self.contact_info['email']} still the best address?",
                "es": f"Para notificaciones por correo, ¿{self.contact_info['email']} sigue siendo la mejor dirección?",
            },
            "fax": {
                "en": f"And lastly, is the fax number {self.contact_info['fax']} correct?",
                "es": f"Y finalmente, ¿el número de fax {self.contact_info['fax']} es correcto?",
            },
        }

        prompt = validation_prompts[next_field]
        next_step = "proceed to next contact field" if len(remaining) > 1 else "proceed to closing"

        return f"""CURRENT TASK: Validate Contact Information ({next_field.upper()})

IMPORTANT: Read slowly and clearly, especially for email and phone numbers.

English: "{prompt['en']}"
Spanish: "{prompt['es']}"

WAIT for confirmation.

SPEAK SLOWLY when reading:
- Email addresses: Spell if needed - "{self.contact_info['email']}" letter by letter if customer asks
- Phone numbers: Read in groups - "{self.contact_info['phone']}" with pauses between segments
- Fax numbers: Same as phone

After confirmation, {next_step}."""

    def _prompt_closing(self, state: ConversationState) -> str:
        en_parts = []
        es_parts = []
        expectation_en = ""
        expectation_es = ""

        if state.collected_info.get("document_request"):
            en_parts.append("send the requested documents via email")
            es_parts.append("enviarle los documentos solicitados por correo")
            expectation_en = " Once you receive it, please let us know how much longer it will take to release the payment."
            expectation_es = " Una vez que lo reciba, por favor háganos saber cuánto tiempo más tomará liberar el pago."

        if state.collected_info.get("payment_details"):
            en_parts.append("note your payment details")
            es_parts.append("registrar los detalles de su pago")

        if state.collected_info.get("follow_up_date"):
            date = state.collected_info["follow_up_date"]
            en_parts.append(f"follow up on {date}")
            es_parts.append(f"dar seguimiento el {date}")

        en_summary = ", and ".join(en_parts) if en_parts else "review your account"
        es_summary = ", y ".join(es_parts) if es_parts else "revisar su cuenta"
        payment_word_en = "check" if state.collected_info.get("payment_details") else "payment"
        payment_word_es = "cheque" if state.collected_info.get("payment_details") else "pago"

        return f"""CURRENT TASK: Close Call (set up expectations)

Summarize ONLY what was actually agreed and set clear expectations for the next step:

English:
"Okay, so we'll be expecting to receive the {payment_word_en} in the next few days. I'll {en_summary} and leave the proper comments on your account.{expectation_en} If you have any questions, please let me know. Have a nice day, and thank you for your time!"

Spanish:
"Muy bien, entonces estaremos esperando recibir el {payment_word_es} en los próximos días. Voy a {es_summary} y dejaré los comentarios correspondientes en su cuenta.{expectation_es} Si tiene alguna pregunta, por favor hágamelo saber. ¡Que tenga un buen día y gracias por su tiempo!"

WAIT for customer confirmation before ending.

RULE: Only summarize what customer explicitly agreed to. Do NOT invent commitments."""


class CompactStagedAgent:
    """Compact staged agent that defers contact validation until the end."""

    def __init__(self, llm: Any, user_data: dict, history_limit: int = 3):
        self.llm = llm
        self.builder = StagedPromptBuilder(user_data)
        self.state = ConversationState()
        # Full base instructions are sent every turn; token savings come from
        # sending only the current stage's prompt instead of all stages at once.
        self.base_instructions = self.builder.get_base_instructions()
        self.history_limit = history_limit

    def _format_short_history(self) -> str:
        hist = self.state.conversation_history[-self.history_limit:]
        if not hist:
            return "No prior conversation."
        lines = []
        for turn in hist:
            role = turn.get("role", "customer").capitalize()
            content = turn.get("content", "").replace("\n", " ")
            if len(content) > 240:
                content = content[:237] + "..."
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    STAGE_ORDER = [
        CallStage.GREETING,
        CallStage.MONITORING_DISCLOSURE,
        CallStage.REASON_FOR_CALL,
        CallStage.INVOICE_REVIEW,
        CallStage.RCA_IDENTIFICATION,
        CallStage.PAYMENT_GATHERING,
        CallStage.CONTACT_VALIDATION,
        CallStage.CLOSING,
        CallStage.COMPLETED,
    ]

    def _core_stages_remaining(self) -> bool:
        """True if we haven't naturally reached the contact-validation point yet."""
        try:
            current_idx = self.STAGE_ORDER.index(self.state.current_stage)
            contact_idx = self.STAGE_ORDER.index(CallStage.CONTACT_VALIDATION)
        except ValueError:
            return True
        return current_idx < contact_idx

    def _contact_validation_needed(self) -> bool:
        """Only validate contact info if it wasn't already completed earlier (this call or a prior one)."""
        if self.builder.contact_info.get("validated"):
            return False
        if self.state.is_stage_complete(CallStage.CONTACT_VALIDATION):
            return False
        return True

    def detect_next_stage(self, customer_response: str) -> CallStage:
        detection_prompt = (
            f"CurrentStage: {self.state.current_stage.value}\n"
            f"Completed: {[s.value for s in self.state.completed_stages]}\n"
            f"ShortHistory:\n{self._format_short_history()}\n"
            f"Customer: {customer_response}\n"
            "Which stage is appropriate next? Reply only with the stage name."
        )

        try:
            resp = self.llm.invoke(detection_prompt).content.strip().lower()
            for stage in CallStage:
                if stage.value == resp:
                    if stage == CallStage.CONTACT_VALIDATION:
                        # Never validate contact before core call content is done.
                        if self._core_stages_remaining():
                            return self._get_next_sequential_stage()
                        # Skip entirely if validation was already completed earlier.
                        if not self._contact_validation_needed():
                            return CallStage.CLOSING
                    return stage
        except Exception:
            pass

        return self._get_next_sequential_stage()

    def _get_next_sequential_stage(self) -> CallStage:
        order = self.STAGE_ORDER
        try:
            i = order.index(self.state.current_stage)
            if i < len(order) - 1:
                nxt = order[i + 1]
                # Skip contact validation entirely if it was already done earlier.
                if nxt == CallStage.CONTACT_VALIDATION and not self._contact_validation_needed():
                    return CallStage.CLOSING
                return nxt
        except ValueError:
            pass
        return CallStage.COMPLETED

    def process_turn(self, customer_message: str) -> str:
        if not self.state.language_detected:
            self.state.language_detected = self._detect_language(customer_message)

        self.state.conversation_history.append({"role": "customer", "content": customer_message})

        next_stage = self.detect_next_stage(customer_message)
        self.state.current_stage = next_stage

        if next_stage == CallStage.COMPLETED:
            self.state.mark_stage_complete(CallStage.COMPLETED)
            return "[Call completed]"

        stage_prompt = self.builder.get_stage_prompt(next_stage, self.state)

        prompt = (
            f"{self.base_instructions}\n\n--- STAGE: {next_stage.value.upper()} ---\n"
            f"{stage_prompt}\n\n"
            f"RecentConversation:\n{self._format_short_history()}\n\n"
            f"Customer just said: \"{customer_message}\"\n\n"
            "Respond naturally and appropriately, following the stage instructions above but adapting to what the customer actually said."
        )

        try:
            reply = self.llm.invoke(prompt).content
        except Exception:
            reply = "I’m having trouble accessing the assistant. Please hold while I try again."
            self.state.current_stage = self._get_next_sequential_stage()

        self._update_state_from_interaction(customer_message, reply, next_stage)
        self.state.conversation_history.append({"role": "agent", "content": reply})

        return reply

    def _detect_language(self, text: str) -> str:
        spanish_indicators = ["sí", "no", "está", "hola", "gracias", "por favor"]
        t = text.lower()
        for i in spanish_indicators:
            if i in t:
                return "Spanish"
        return "English"

    def _update_state_from_interaction(self, customer_msg: str, agent_reply: str, stage: CallStage):
        lmsg = customer_msg.lower()
        lreply = agent_reply.lower()

        if stage == CallStage.GREETING and ("yes" in lmsg or "sí" in lmsg or "i am" in lmsg):
            self.state.verified_poc = True
            self.state.mark_stage_complete(CallStage.GREETING)

        if stage == CallStage.MONITORING_DISCLOSURE and "recorded" in lreply:
            self.state.monitoring_disclosed = True
            self.state.mark_stage_complete(CallStage.MONITORING_DISCLOSURE)

        if stage == CallStage.REASON_FOR_CALL and "past due" in lreply:
            self.state.mark_stage_complete(CallStage.REASON_FOR_CALL)

        if stage == CallStage.INVOICE_REVIEW:
            for inv in self.builder.invoices:
                inv_num = str(inv.get("invoice_number", "")).lower()
                if inv_num and inv_num in lmsg and inv_num not in self.state.invoices_discussed:
                    self.state.invoices_discussed.append(inv_num)

        if stage == CallStage.RCA_IDENTIFICATION and any(k in lmsg for k in ["because", "dispute", "paid", "pending", "approval"]):
            self.state.rca_identified = True
            self.state.update_collected_info("rca", customer_msg)
            self.state.mark_stage_complete(CallStage.RCA_IDENTIFICATION)

        if stage == CallStage.PAYMENT_GATHERING and ("check" in lmsg or "ach" in lmsg or "$" in customer_msg):
            self.state.payment_details_gathered = True
            self.state.update_collected_info("payment_details", customer_msg)
            self.state.mark_stage_complete(CallStage.PAYMENT_GATHERING)

        if stage == CallStage.CONTACT_VALIDATION:
            if "name" in lmsg:
                self.state.contacts_validated["name"] = True
            if "phone" in lmsg:
                self.state.contacts_validated["phone"] = True
            if "email" in lmsg:
                self.state.contacts_validated["email"] = True
            if all(self.state.contacts_validated.values()):
                self.state.mark_stage_complete(CallStage.CONTACT_VALIDATION)


def create_staged_agent_two(llm, user_data: dict) -> CompactStagedAgent:
    return CompactStagedAgent(llm, user_data)


def get_prompt(user_data: dict) -> str:
    """Single-shot system prompt containing all stages, in order, with
    contact validation placed right before closing (deferred to the end)."""
    builder = StagedPromptBuilder(user_data)
    state = ConversationState()
    base = builder.get_base_instructions()

    stage_order = [
        CallStage.GREETING,
        CallStage.MONITORING_DISCLOSURE,
        CallStage.REASON_FOR_CALL,
        CallStage.INVOICE_REVIEW,
        CallStage.RCA_IDENTIFICATION,
        CallStage.PAYMENT_GATHERING,
        CallStage.CONTACT_VALIDATION,
        CallStage.CLOSING,
    ]

    stages_text = []
    for i, stage in enumerate(stage_order, 1):
        stage_prompt = builder.get_stage_prompt(stage, state)
        stages_text.append(f"--- STAGE {i}: {stage.value.upper().replace('_', ' ')} ---\n{stage_prompt}")

    all_stages = "\n\n".join(stages_text)

    return f"""{base}

CALL FLOW — Follow these stages IN ORDER. Complete each stage before moving to the next.
Only skip a stage if the customer has already provided that information.
Contact validation (Stage 7) should ONLY be asked at the end of the call, right before closing,
and ONLY if it was not already completed earlier in the conversation or on a previous call.
If the customer interrupts with a question or concern, address it before resuming the flow.
Ask ONE question at a time. WAIT for the customer to respond before continuing.

{all_stages}

OVERALL RULES:
- Start with Stage 1 (Greeting) immediately when the call begins
- This is a FOLLOW-UP call: reference the previous conversation and keep a respectful sense of urgency
- Never ask for credit card or card payment details. If the customer tries to pay by card, don't say cards aren't accepted — simply note their payment details and keep moving
- Progress through stages naturally based on customer responses
- NEVER repeat information already shared or acknowledged
- If customer provides information out of order (e.g., mentions payment early), capture it and skip that stage later
- Only validate contact information once, at the end, before closing — and disregard it entirely if it was already done in Call 1 or within the last 6 months
- Match the customer's language (English/Spanish) throughout
- Be conversational and human — never sound robotic or scripted"""
