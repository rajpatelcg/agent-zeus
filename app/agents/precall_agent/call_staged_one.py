"""
Staged Collections Call Prompt System with LangChain
- Sends prompts in stages (not all at once)
- LLM-based stage detection and routing
- Smart fallback mechanisms to avoid repetition
- Context-aware Q&A
- Token-optimized
"""

import datetime
import json
from typing import Optional, Dict, List, Any
from enum import Enum

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser

try:
    from .tools.scenario_profile import format_scenario_profile_for_prompt
except Exception:  # pragma: no cover - optional dependency / direct execution
    format_scenario_profile_for_prompt = None


class CallStage(Enum):
    """Call stages for collections workflow"""
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
    """Tracks conversation state to avoid repetition"""
    
    def __init__(self):
        self.current_stage = CallStage.GREETING
        self.completed_stages = set()
        self.collected_info = {}
        self.verified_poc = False
        self.monitoring_disclosed = False
        self.invoices_discussed = []
        self.rca_identified = False
        self.payment_details_gathered = False
        self.contacts_validated = {
            "name": False,
            "phone": False,
            "email": False,
            "fax": False
        }
        self.language_detected = None
        self.conversation_history = []
        
    def mark_stage_complete(self, stage: CallStage):
        """Mark a stage as completed"""
        self.completed_stages.add(stage)
        
    def is_stage_complete(self, stage: CallStage) -> bool:
        """Check if stage already completed"""
        return stage in self.completed_stages
    
    def update_collected_info(self, key: str, value: Any):
        """Store collected information"""
        self.collected_info[key] = value
        
    def has_info(self, key: str) -> bool:
        """Check if information already collected"""
        return key in self.collected_info and self.collected_info[key]


class StagedPromptBuilder:
    """Builds context-aware prompts for each stage"""
    
    def __init__(self, user_data: dict):
        self.user_data = user_data
        self.today = datetime.datetime.now().strftime("%B %d, %Y")
        
        # Extract common fields once
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
    
    def _prepare_invoices(self) -> List[Dict]:
        invoice_details = self.user_data.get("invoice_details", [])
        if not invoice_details:
            return [{
                "invoice_number": self.user_data.get("invoice_number", "Unknown"),
                "outstanding_balance": self.total_amount,
                "due_date": self.user_data.get("due_date", "Unknown"),
                "days_past_due": self.user_data.get("days_past_due", "Unknown")
            }]
        
        prepared = []
        for inv in invoice_details[:7]:  # Limit to 7 invoices
            if isinstance(inv, dict):
                prepared.append({
                    "invoice_number": inv.get("invoice_number", "Unknown"),
                    "outstanding_balance": inv.get("outstanding_balance", 0),
                    "due_date": inv.get("due_date", "Unknown"),
                    "days_past_due": inv.get("days_past_due", "Unknown")
                })
        return prepared
    
    def _get_contact_info(self) -> Dict:
        return {
            "phone": self.user_data.get("phone_number", "Unknown"),
            "extension": self.user_data.get("phone_extension", "Unknown"),
            "email": self.user_data.get("email_address", "Unknown"),
            "fax": self.user_data.get("fax_number", "Unknown"),
            "validated": bool(self.user_data.get("contact_validation_completed", False)),
            "validation_date": self.user_data.get("contact_validation_date", "Unknown")
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
    
    SCENARIO_SELF_DETECT = (
        "SCENARIO (self-detect): No scenario profile was pre-detected. Infer the customer's situation "
        "from their responses — payment due, invoice/document request, escalation, billing dispute, "
        "partial payment, or refusal/cannot pay — and adapt your tone accordingly: firm-but-respectful "
        "for past due, helpful for document requests, calm de-escalating for escalations, patient and "
        "non-defensive for disputes, collaborative for partial payment, empathetic and low-pressure for "
        "refusal/hardship. Re-read the situation each turn and shift tone smartly if it changes."
    )

    def get_base_instructions(self) -> str:
        """Core instructions sent with every stage"""
        scenario_block = (
            f"\n\n{self.scenario_behavior}"
            if self.scenario_behavior
            else f"\n\n{self.SCENARIO_SELF_DETECT}"
        )
        return f"""You are {self.collector_name}, a Collections Analyst at ODP Business Group. Today is {self.today}.

Bilingual (English/Spanish, incl. Caribbean/Puerto Rican). Match the customer's language with no announcement; default to English until they speak Spanish.

Be conversational and human, never robotic or scripted. Ask ONE question at a time, listen before proceeding, never repeat info already shared, and adapt to what the customer says.

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
- While verifying the conatct details like reading an EMAIL ADDRESS, PHONE NUMBER, EXTENSION, FAX, or ACCOUNT/INVOICE NUMBER, slow right down — say it in small chunks, pause between groups of digits, and offer to spell it out or repeat it.
- Speak DATES the natural way a person would say them out loud (e.g. "January fifteenth" not "January 15, 2026", "the fifteenth of this month", "next Tuesday") — never read a raw date string.
- Speak DOLLAR AMOUNTS naturally too (e.g. "fifteen hundred dollars" or "one thousand five hundred"), not "$1500.00".
- After giving important details, give the customer a beat to catch up before moving on.

Use the scenario profile below only as tone guidance; let the customer's actual words and situation lead, and shift your tone smartly if the situation changes mid-call.{scenario_block}"""

    def get_stage_prompt(self, stage: CallStage, state: ConversationState) -> str:
        """Get prompt for specific stage with fallback logic"""
        
        # Base prompt method mapping
        stage_methods = {
            CallStage.GREETING: self._prompt_greeting,
            CallStage.MONITORING_DISCLOSURE: self._prompt_monitoring,
            CallStage.REASON_FOR_CALL: self._prompt_reason,
            CallStage.INVOICE_REVIEW: self._prompt_invoice_review,
            CallStage.RCA_IDENTIFICATION: self._prompt_rca,
            CallStage.PAYMENT_GATHERING: self._prompt_payment,
            CallStage.CONTACT_VALIDATION: self._prompt_contact_validation,
            CallStage.CLOSING: self._prompt_closing
        }
        
        method = stage_methods.get(stage)
        if method:
            return method(state)
        return ""
    
    def _prompt_greeting(self, state: ConversationState) -> str:
        """Stage 1: Greeting and POC verification"""
        return f"""TASK: Greet and verify you're speaking with {self.ap_name} at {self.customer_name}.

EN: "Hi, good day, this is {self.collector_name}, calling from ODP Business Group. I'm looking for {self.ap_name}, please."
ES: "Hola, buen día, le habla {self.collector_name}, llamando de ODP Business Group. Busco a {self.ap_name}, por favor."

If wrong person: ask for their availability or an alternate contact, then end the call politely.
If asked what the call is about: "I'm calling from ODP Business Group regarding an administrative matter. I can share details once I confirm I'm speaking with the right person."

Do not reveal balance, invoices, dates, or account details until the person is verified."""

    def _prompt_monitoring(self, state: ConversationState) -> str:
        """Stage 2: Call monitoring disclosure"""
        if state.monitoring_disclosed:
            return "SKIP: Monitoring disclosure already completed."

        return f"""TASK: Disclose call monitoring in a natural, offhand way, only after {self.ap_name} is verified. Keep it light — like a quick heads-up, not a legal announcement.

EN (paraphrase naturally): "Just so you know, this call may be recorded for quality and training."
ES (paraphrase naturally): "Solo para que sepa, esta llamada puede ser grabada con fines de calidad y entrenamiento."
"""

    def _prompt_reason(self, state: ConversationState) -> str:
        """Stage 3: Reason for call"""
        return f"""TASK: State the reason for the call, then wait for the response.

EN: "The reason for this call is to let you know there is a past due balance of ${self.total_amount:.2f} on your account #{self.account_number}, and we'd like to get payment details. Can I give you the invoice number so we can review the status of each invoice?"
ES: "El motivo de esta llamada es informarle que hay un balance vencido de ${self.total_amount:.2f} en su cuenta #{self.account_number}, y nos gustaría obtener los detalles de pago. ¿Le puedo dar el número de factura para revisar el estado de cada factura?"
"""

    def _prompt_invoice_review(self, state: ConversationState) -> str:
        """Stage 4: Invoice review - ONE at a time"""
        discussed = state.invoices_discussed
        remaining = [inv for inv in self.invoices if inv["invoice_number"] not in discussed]

        if not remaining:
            return "All invoices discussed. Proceed to next stage."

        inv = remaining[0]
        position = len(discussed) + 1
        total = len(self.invoices)
        ordinal_en = "first" if position == 1 else "next"
        ordinal_es = "primera" if position == 1 else "siguiente"

        ack_instruction = (
            "move to the next invoice"
            if len(remaining) > 1
            else 'all invoices are now shared, so ask why they weren\'t paid on time (e.g. "Is there any reason these weren\'t paid on time?") and note the delay reason before moving on'
        )

        return f"""TASK: Share invoice {position} of {total}.

EN: "The {ordinal_en} invoice number is {inv['invoice_number']}, for ${inv['outstanding_balance']}, due {inv['due_date']}, {inv['days_past_due']} days past due."
ES: "La {ordinal_es} factura es la número {inv['invoice_number']}, por ${inv['outstanding_balance']}, con vencimiento {inv['due_date']}, con {inv['days_past_due']} días de atraso."

Handle dispute/paid/missing-PO questions naturally. If asked for a statement/copies, offer to send and confirm email, then return to payment timing.
On acknowledgment: {ack_instruction}. If already mentioned, don't repeat."""

    def _prompt_rca(self, state: ConversationState) -> str:
        """Stage 5: Root cause analysis"""
        if state.rca_identified:
            return "RCA already identified. Proceed based on identified reason."

        previous_rca = self.user_data.get("delay_reason", "")
        rca_hint = f"\nPrevious delay reason on file: {previous_rca}" if previous_rca and previous_rca != "Unknown" else ""
        plural = len(self.invoices) > 1
        subject_en = "these invoices were" if plural else "this invoice was"
        subject_es = "estas facturas no se pagaron" if plural else "esta factura no se pagó"

        return f"""TASK: Ask why {subject_en} not paid on time, then listen carefully and CAPTURE the delay reason. Always ask this — never skip it — and make sure you note down the reason the customer gives.{rca_hint}

EN: "Is there any reason why {subject_en} not paid on time?"
ES: "¿Hay alguna razón por la cual {subject_es} a tiempo?"

Once they explain, briefly repeat the reason back to confirm you've got it right (e.g. "Okay, so it's held up in approval — got it") before moving on. This confirms the delay reason is recorded.

PAYMENT DATE: When payment is still outstanding, ask: "What date can you realistically make the payment?" Accept only a date from tomorrow through 30 days from today. If the customer suggests an earlier or later date, explain that you need the earliest realistic date within that window and ask again. Do not invent a date if the customer cannot commit to one.

Based on what they say, offer the right solution or take the proper action for that specific reason — ask only the follow-up questions needed for that action item, and never re-ask anything already answered:
dispute → what's wrong, which invoice, undisputed amount. Missing docs → offer copies, confirm email. Approval pending → approver and release date. Missing PO → which invoices and what's needed. Hardship → earliest date or partial amount. Already paid/scheduled → payment details."""

    def _prompt_payment(self, state: ConversationState) -> str:
        """Stage 6: Payment details gathering"""
        if state.payment_details_gathered:
            return "Payment details already collected."

        rca = state.collected_info.get("rca", "").lower()
        already_paid = "already paid" in rca or "paid" in rca or "sent" in rca or "scheduled" in rca
        if not already_paid:
            # Only collect payment details if the customer says they've already paid.
            return "No payment to gather — customer has not paid yet. Do not ask for payment details; proceed based on the customer's situation."

        reference_line = (
            '- Reference/check/ACH#: EN "What is the check number or payment reference?" / ES "¿Cuál es el número de cheque o referencia del pago?"\n'
        )

        return f"""TASK: The customer says the payment was ALREADY made — gather the details to confirm it. Only run this stage when payment has already been sent; never ask these for a future promise-to-pay. Ask one question at a time and skip any already answered.

Opener: EN "Do you have the payment details you can share with me?" / ES "¿Tiene los detalles del pago que me pueda compartir?"
{reference_line}- Amount: EN "For how much is the payment?" / ES "¿Por cuánto es el pago?"
- Date released: EN "When was it released?" / ES "¿Cuándo fue liberado?"
- Invoices covered: EN "Which invoices does this payment cover?" / ES "¿Qué facturas cubre este pago?\""""

    def _prompt_contact_validation(self, state: ConversationState) -> str:
        """Stage 7: Contact validation - slow and careful"""
        if self.contact_info["validated"]:
            date = self.contact_info["validation_date"]
            return f"""Contact info was already validated{f' on {date}' if date != 'Unknown' else ''}. Don't re-validate unless the customer offers updates. Proceed to closing."""

        remaining = [k for k, v in state.contacts_validated.items() if not v]
        if not remaining:
            return "All contacts validated. Proceed to closing."

        next_field = remaining[0]
        prompts = {
            "name": (
                f"Before I let you go, I'd like to confirm your name and last name are correct - is it {self.ap_name}?",
                f"Antes de dejarle ir, me gustaría confirmar que su nombre y apellido son correctos - ¿es {self.ap_name}?",
            ),
            "phone": (
                f"And is {self.contact_info['phone']} extension {self.contact_info['extension']} still the best phone number to reach you?",
                f"¿Y el número {self.contact_info['phone']} extensión {self.contact_info['extension']} sigue siendo el mejor teléfono para contactarle?",
            ),
            "email": (
                f"For email notifications, is {self.contact_info['email']} still the best address?",
                f"Para notificaciones por correo, ¿{self.contact_info['email']} sigue siendo la mejor dirección?",
            ),
            "fax": (
                f"And lastly, is the fax number {self.contact_info['fax']} correct?",
                f"Y finalmente, ¿el número de fax {self.contact_info['fax']} es correcto?",
            ),
        }
        en, es = prompts[next_field]
        next_step = "move to the next contact field" if len(remaining) > 1 else "proceed to closing"

        return f"""TASK: Validate {next_field} — slow WAY down here. Read the email and phone/fax numbers in small chunks with pauses, and offer to spell out the name/email or repeat the digits so the customer can confirm each part.

EN: "{en}"
ES: "{es}"

After confirmation, {next_step}."""

    def _prompt_closing(self, state: ConversationState) -> str:
        """Stage 8: Closing"""
        parts = []
        expectation_en = ""
        expectation_es = ""
        if state.collected_info.get("document_request"):
            parts.append("send the requested documents via email")
            expectation_en = " Once you receive it, please let us know how much longer it will take to release the payment."
            expectation_es = " Una vez que lo reciba, por favor háganos saber cuánto tiempo más tomará liberar el pago."
        if state.collected_info.get("payment_details"):
            parts.append("noted your payment details")
        if state.collected_info.get("follow_up_date"):
            parts.append(f"follow up on {state.collected_info['follow_up_date']}")
        summary = ", and ".join(parts) if parts else "discuss your account"

        return f"""TASK: Close the call, summarizing ONLY what was actually agreed and setting clear expectations for the next step. Wait for confirmation before ending. Do not invent commitments.

EN: "Thank you so much for your time today. As we discussed, I'll {summary}.{expectation_en} Is there anything else I can help you with?" (If no) "Have a wonderful day, goodbye!"
ES: "Muchas gracias por su tiempo hoy. Como conversamos, {summary}.{expectation_es} ¿Hay algo más en lo que le pueda ayudar?" (Si no) "Que tenga un excelente día, ¡hasta luego!"
"""


class StagedCollectionsAgent:
    """Main agent class with LLM-based stage detection"""
    
    def __init__(self, llm, user_data: dict):
        self.llm = llm
        self.user_data = user_data
        self.prompt_builder = StagedPromptBuilder(user_data)
        self.state = ConversationState()
        self.parser = StrOutputParser()
        
    def detect_next_stage(self, customer_response: str) -> CallStage:
        """Use LLM to detect which stage to proceed to based on conversation"""
        
        detection_prompt = f"""Based on the conversation context and customer response, determine the next appropriate stage.

Current stage: {self.state.current_stage.value}
Completed stages: {[s.value for s in self.state.completed_stages]}
Customer said: "{customer_response}"

POC Verified: {self.state.verified_poc}
Monitoring Disclosed: {self.state.monitoring_disclosed}
RCA Identified: {self.state.rca_identified}
Payment Details Gathered: {self.state.payment_details_gathered}

Available stages (in order):
1. greeting - If not verified POC yet
2. monitoring_disclosure - After POC verified, before discussing account
3. reason_for_call - After disclosure, before invoice details
4. invoice_review - Share invoice details
5. rca_identification - Ask why invoices unpaid
6. payment_gathering - Get payment details if payment mentioned
7. contact_validation - Validate contact info before closing
8. closing - Wrap up call
9. completed - Call ended

Rules:
- Skip stages already completed
- If customer interrupts with question/concern, stay in current stage to address it
- If customer provides payment info unprompted, go to payment_gathering
- If customer mentions dispute/issue, stay in rca_identification
- Always validate contacts before closing
- If call should end, return 'completed'

Return ONLY the stage name (e.g., 'invoice_review'), nothing else."""

        # Call LLM for stage detection
        try:
            next_stage_name = self.llm.invoke(detection_prompt).content.strip().lower()
            
            # Map to enum
            for stage in CallStage:
                if stage.value == next_stage_name:
                    return stage
            
            # Fallback: proceed sequentially
            return self._get_next_sequential_stage()
        except:
            # If LLM fails, fall back to sequential
            return self._get_next_sequential_stage()
    
    def _get_next_sequential_stage(self) -> CallStage:
        """Fallback: Sequential stage progression"""
        stage_order = [
            CallStage.GREETING,
            CallStage.MONITORING_DISCLOSURE,
            CallStage.REASON_FOR_CALL,
            CallStage.INVOICE_REVIEW,
            CallStage.RCA_IDENTIFICATION,
            CallStage.PAYMENT_GATHERING,
            CallStage.CONTACT_VALIDATION,
            CallStage.CLOSING,
            CallStage.COMPLETED
        ]
        
        try:
            current_index = stage_order.index(self.state.current_stage)
            if current_index < len(stage_order) - 1:
                return stage_order[current_index + 1]
        except ValueError:
            pass
        
        return CallStage.COMPLETED
    
    def process_turn(self, customer_message: str) -> str:
        """Process one turn of conversation"""
        
        # Detect language if not yet detected
        if not self.state.language_detected:
            self.state.language_detected = self._detect_language(customer_message)
        
        # Update conversation history
        self.state.conversation_history.append({
            "role": "customer",
            "content": customer_message
        })
        
        # Detect next stage based on customer response
        next_stage = self.detect_next_stage(customer_message)
        self.state.current_stage = next_stage
        
        if next_stage == CallStage.COMPLETED:
            return "[Call completed]"
        
        # Build prompt for current stage
        base_instructions = self.prompt_builder.get_base_instructions()
        stage_prompt = self.prompt_builder.get_stage_prompt(next_stage, self.state)
        
        # Build chat history for context
        history_messages = []
        for turn in self.state.conversation_history[-6:]:  # Last 6 turns for context
            if turn["role"] == "customer":
                history_messages.append(HumanMessage(content=turn["content"]))
            else:
                history_messages.append(AIMessage(content=turn["content"]))
        
        # Construct full prompt
        full_prompt = f"""{base_instructions}

{stage_prompt}

Recent conversation context:
{self._format_history(history_messages)}

Customer just said: "{customer_message}"

Respond naturally and appropriately. Follow the stage instructions but adapt to what customer actually said.

CRITICAL RULES:
- If customer already provided information, acknowledge it - don't ask again
- If customer asks a question, answer it naturally before proceeding
- If customer interrupts with concern, address it immediately
- Stay in character as {self.prompt_builder.collector_name}
- Match customer's language (English/Spanish)
- Ask only ONE question at a time
- Be human, conversational, professional"""
        
        # Get LLM response
        response = self.llm.invoke(full_prompt).content
        
        # Update state based on response
        self._update_state_from_response(customer_message, response, next_stage)
        
        # Add to history
        self.state.conversation_history.append({
            "role": "agent",
            "content": response
        })
        
        return response
    
    def _detect_language(self, text: str) -> str:
        """Detect if customer is speaking Spanish or English"""
        spanish_indicators = ["sí", "no", "está", "hola", "gracias", "por favor"]
        text_lower = text.lower()
        
        for indicator in spanish_indicators:
            if indicator in text_lower:
                return "Spanish"
        
        return "English"
    
    def _format_history(self, messages) -> str:
        """Format message history for context"""
        if not messages:
            return "No previous conversation"
        
        formatted = []
        for msg in messages:
            role = "Customer" if isinstance(msg, HumanMessage) else "Agent"
            formatted.append(f"{role}: {msg.content[:100]}...")
        
        return "\n".join(formatted)
    
    def _update_state_from_response(self, customer_msg: str, agent_response: str, stage: CallStage):
        """Update conversation state based on interaction"""
        
        customer_lower = customer_msg.lower()
        response_lower = agent_response.lower()
        
        # Mark stages as complete
        if stage == CallStage.GREETING and ("speaking" in customer_lower or "yes" in customer_lower):
            self.state.verified_poc = True
            self.state.mark_stage_complete(CallStage.GREETING)
        
        if stage == CallStage.MONITORING_DISCLOSURE and "recorded" in response_lower:
            self.state.monitoring_disclosed = True
            self.state.mark_stage_complete(CallStage.MONITORING_DISCLOSURE)
        
        if stage == CallStage.REASON_FOR_CALL and "past due" in response_lower:
            self.state.mark_stage_complete(CallStage.REASON_FOR_CALL)
        
        if stage == CallStage.INVOICE_REVIEW:
            # Track which invoices discussed
            for inv in self.prompt_builder.invoices:
                inv_num = inv["invoice_number"]
                if inv_num in response_lower and inv_num not in self.state.invoices_discussed:
                    self.state.invoices_discussed.append(inv_num)
        
        if stage == CallStage.RCA_IDENTIFICATION and any(
            keyword in customer_lower for keyword in ["because", "reason", "dispute", "paid", "pending"]
        ):
            self.state.rca_identified = True
            self.state.update_collected_info("rca", customer_msg)
            self.state.mark_stage_complete(CallStage.RCA_IDENTIFICATION)
        
        if stage == CallStage.PAYMENT_GATHERING:
            # Check for payment details in customer response
            if "check" in customer_lower or "ach" in customer_lower or "$" in customer_msg:
                self.state.payment_details_gathered = True
                self.state.update_collected_info("payment_details", customer_msg)
                self.state.mark_stage_complete(CallStage.PAYMENT_GATHERING)
        
        if stage == CallStage.CONTACT_VALIDATION:
            # Check which contact was validated
            if "name" in response_lower:
                self.state.contacts_validated["name"] = True
            if "phone" in response_lower:
                self.state.contacts_validated["phone"] = True
            if "email" in response_lower:
                self.state.contacts_validated["email"] = True
            if "fax" in response_lower:
                self.state.contacts_validated["fax"] = True
            
            if all(self.state.contacts_validated.values()):
                self.state.mark_stage_complete(CallStage.CONTACT_VALIDATION)
        
        if stage == CallStage.CLOSING and ("goodbye" in response_lower or "hasta luego" in response_lower):
            self.state.mark_stage_complete(CallStage.CLOSING)
            self.state.current_stage = CallStage.COMPLETED


def create_staged_agent(llm, user_data: dict) -> StagedCollectionsAgent:
    """Factory function to create staged agent"""
    return StagedCollectionsAgent(llm, user_data)


def get_prompt(user_data: dict) -> str:
    """
    Build a single system prompt containing all staged instructions.
    Compatible with caller.py's Pipecat pipeline (OpenAILLMContext).
    The LLM follows the stages sequentially within the conversation.
    """
    builder = StagedPromptBuilder(user_data)
    state = ConversationState()

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

    all_stages = "\n\n".join(
        f"--- STAGE {i}: {stage.value.upper().replace('_', ' ')} ---\n{builder.get_stage_prompt(stage, state)}"
        for i, stage in enumerate(stage_order, 1)
    )

    return f"""{builder.get_base_instructions()}

CALL FLOW — follow these stages IN ORDER, skipping a stage only if the customer already gave that info. If they interrupt with a question or concern, address it before resuming.

{all_stages}

RULES: Start at Stage 1 immediately. Never ask for credit card or card payment details. If the customer tries to pay by card, don't say you don't accept cards — simply note their payment details and keep the conversation moving. If the customer gives information out of order, capture it and skip that stage later."""


# Example usage
if __name__ == "__main__":
    from langchain_openai import AzureChatOpenAI
    
    # Sample user data
    sample_data = {
        "collector_first_name": "LISA",
        "collector_last_name": "GARCIA",
        "customer_name": "ACME Corp",
        "accounts_payable_name": "John Smith",
        "account_number": "ACC12345",
        "phone_number": "555-0100",
        "phone_extension": "101",
        "email_address": "john.smith@acme.com",
        "fax_number": "555-0199",
        "invoice_details": [
            {
                "invoice_number": "INV001",
                "outstanding_balance": 1500.00,
                "due_date": "January 15, 2026",
                "days_past_due": 30
            },
            {
                "invoice_number": "INV002",
                "outstanding_balance": 2500.00,
                "due_date": "January 20, 2026",
                "days_past_due": 25
            }
        ],
        "language": "English",
        "contact_validation_completed": False
    }
    
    # Initialize LLM (configure with your Azure OpenAI settings)
    llm = AzureChatOpenAI(
        temperature=0.7,
        model_name="gpt-4"
    )
    
    # Create agent
    agent = create_staged_agent(llm, sample_data)
    
    # Simulate conversation
    print("=== Staged Collections Call Demo ===\n")
    
    # Turn 1
    response = agent.process_turn("Hello?")
    print(f"Agent: {response}\n")
    
    # Turn 2
    response = agent.process_turn("Yes, this is John speaking.")
    print(f"Agent: {response}\n")
    
    # Turn 3
    response = agent.process_turn("Okay, I understand.")
    print(f"Agent: {response}\n")
    
    # Turn 4
    response = agent.process_turn("Yes, please share the invoices.")
    print(f"Agent: {response}\n")
