# import os
# import json
# from typing import List, Optional, Any
# from dotenv import load_dotenv
# from langchain_openai import AzureChatOpenAI
# from langchain_core.prompts import ChatPromptTemplate
# from langchain_core.callbacks import BaseCallbackHandler
# from langchain_core.outputs import LLMResult
# from pydantic import BaseModel, Field

# load_dotenv()


# class TokenUsageCallback(BaseCallbackHandler):
#     """Lightweight callback to capture token usage from LLM responses."""
#     def __init__(self):
#         self.prompt_tokens = 0
#         self.completion_tokens = 0
#         self.total_tokens = 0

#     def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
#         if response.llm_output and "token_usage" in response.llm_output:
#             usage = response.llm_output["token_usage"]
#             self.prompt_tokens += usage.get("prompt_tokens", 0)
#             self.completion_tokens += usage.get("completion_tokens", 0)
#             self.total_tokens += usage.get("total_tokens", 0)

# # Strict allowed fields per action_id — ALL fields always included, "" if not found
# ACTION_SCHEMA = {
#     "ACT001": ["action_id", "action_type", "amount", "date", "notes", "description", "delivery_channel"],
#     "ACT002": ["action_id", "action_type", "notes", "description", "escalation_level", "escalated_to_role", "reason", "target_resolution_date", "sla_hours", "due_by"],
#     "ACT003": ["action_id", "action_type", "notes", "description", "dispute_reason", "invoice_number", "resolution_status"],
#     "ACT004": ["action_id", "action_type", "notes", "description", "documents_requested", "document_type", "delivery_channel"],
#     "ACT005": ["action_id", "action_type", "amount", "date", "notes", "description", "payment_method"],
#     "ACT006": ["action_id", "action_type", "notes", "description", "settlement_amount", "settlement_due_date"],
#     "ACT007": ["action_id", "action_type", "notes", "description", "requested_amount", "reason", "requested_date"],
#     "ACT008": ["action_id", "action_type", "notes", "description", "request_details", "preferred_time"],
# }


# class CallAction(BaseModel):
#     # --- Core (all actions) ---
#     action_id: str = Field(description="Unique action ID: ACT001 to ACT008.")
#     action_type: str = Field(description="PromiseToPay, Escalation, Dispute, DocumentCopy, PartialPayment, DoubtfulReceivable, CreditRequest, or OtherCustomerRequest.")
#     notes: Optional[str] = Field(None, description="Verbatim or close paraphrase from the transcript that triggered this action.")
#     description: Optional[str] = Field(None, description="Short human-readable description of the action.")

#     # --- ACT001: PromiseToPay ---
#     amount: Optional[float] = Field(None, description="ACT001/ACT005: Amount promised or paid.")
#     date: Optional[str] = Field(None, description="ACT001/ACT005: Date by which payment will be made.")
#     delivery_channel: Optional[str] = Field(None, description="ACT001/ACT004: e.g., Email. Default to 'Email'.")

#     # --- ACT002: Escalation ---
#     escalation_level: Optional[str] = Field(None, description="ACT002: e.g., L1, L2.")
#     escalated_to_role: Optional[str] = Field(None, description="ACT002: Role escalated to, e.g., Manager, Senior Team Member.")
#     reason: Optional[str] = Field(None, description="ACT002/ACT007: Reason for escalation or credit request.")
#     target_resolution_date: Optional[str] = Field(None, description="ACT002: Expected resolution date.")
#     sla_hours: Optional[int] = Field(None, description="ACT002: SLA in hours, e.g., 24.")
#     due_by: Optional[str] = Field(None, description="ACT002: Callback or resolution deadline.")

#     # --- ACT003: Dispute ---
#     dispute_reason: Optional[str] = Field(None, description="ACT003: Specific reason for the dispute.")
#     invoice_number: Optional[str] = Field(None, description="ACT003: Invoice number being disputed.")
#     resolution_status: Optional[str] = Field(None, description="ACT003: e.g., Pending, Under Review, Resolved.")

#     # --- ACT004: DocumentCopy ---
#     documents_requested: Optional[List[str]] = Field(None, description="ACT004: List of documents requested.")
#     document_type: Optional[str] = Field(None, description="ACT004: e.g., Invoice, Receipt, Statement.")

#     # --- ACT005: PartialPayment ---
#     payment_method: Optional[str] = Field(None, description="ACT005: e.g., Bank Transfer, UPI, Credit Card.")

#     # --- ACT006: DoubtfulReceivable ---
#     settlement_amount: Optional[float] = Field(None, description="ACT006: Settlement amount discussed.")
#     settlement_due_date: Optional[str] = Field(None, description="ACT006: Settlement due date.")

#     # --- ACT007: CreditRequest ---
#     requested_amount: Optional[float] = Field(None, description="ACT007: Credit or discount amount requested.")
#     requested_date: Optional[str] = Field(None, description="ACT007: Date by which credit/adjustment is requested.")

#     # --- ACT008: OtherCustomerRequest ---
#     request_details: Optional[str] = Field(None, description="ACT008: Detailed description of the customer's specific request.")
#     preferred_time: Optional[str] = Field(None, description="ACT008: Preferred time for callback or contact.")

#     def to_strict_dict(self) -> dict:
#         """Return ONLY the allowed fields for this action's action_id.
#         All fields are always present — uses empty string '' if not found."""
#         allowed_fields = ACTION_SCHEMA.get(self.action_id, [])
#         raw = self.dict()
#         result = {}
#         for field in allowed_fields:
#             val = raw.get(field)
#             if val is None:
#                 result[field] = ""
#             else:
#                 result[field] = val
#         return result


# class PostCallAnalysis(BaseModel):
#     summary: str = Field(description="A 2-3 sentence summary of the call transcript.")
#     user_action: str = Field(description="The action item that the USER (customer) needs to take after the call.")
#     agent_action_item: str = Field(description="The action item that the AGENT needs to take after the call.")
#     category: str = Field(description="Categorize the outcome: 'pending', 'cleared', or 'dispute'.")
#     actions: List[CallAction] = Field(default_factory=list, description="List of all identified actions.")


# async def process_transcript_and_update_json(call_data: dict):
#     llm = AzureChatOpenAI(
#         azure_endpoint=os.getenv("AZURE_GPT4_API_ENDPOINT", os.getenv("IND_AZURE_ENDPOINT")),
#         api_key=os.getenv("AZURE_GPT4_API_KEY", os.getenv("IND_AZURE_API_KEY")),
#         azure_deployment=os.getenv("AZURE_GPT4_API_DEPLOYMENT", "gpt-4o"),
#         api_version=os.getenv("AZURE_GPT4_API_VERSION", "2025-01-01-preview"),
#         temperature=0,
#     )

#     structured_llm = llm.with_structured_output(PostCallAnalysis)

#     system_prompt = """You are an AI assistant that extracts structured action items from collection call transcripts.
# Analyze the FULL transcript carefully and create a SEPARATE action entry for EVERY distinct customer request. Do not merge multiple requests into one action.

# | action_id | action_type          | Key Fields to Populate                                                                      |
# |-----------|----------------------|---------------------------------------------------------------------------------------------|
# | ACT001    | PromiseToPay         | amount, date, delivery_channel                                                              |
# | ACT002    | Escalation           | escalation_level, escalated_to_role, reason, target_resolution_date, sla_hours, due_by     |
# | ACT003    | Dispute              | dispute_reason, invoice_number, resolution_status                                           |
# | ACT004    | DocumentCopy         | documents_requested, document_type, delivery_channel                                        |
# | ACT005    | PartialPayment       | amount, date, payment_method                                                                |
# | ACT006    | DoubtfulReceivable   | settlement_amount, settlement_due_date                                                      |
# | ACT007    | CreditRequest        | requested_amount, reason, requested_date                                                    |
# | ACT008    | OtherCustomerRequest | request_details, preferred_time                                                             |

# ## CLASSIFICATION RULES (STRICTLY FOLLOW):

# ### ACT001 — PromiseToPay:
# - ONLY when customer makes a CLEAR, SPECIFIC, COMMITTED promise to pay the FULL balance.
# - Must name a specific date they are committing to. E.g., "I'll pay the full $1,220 by Friday."
# - DO NOT use for vague statements: "I might pay in 2-3 months", "maybe later", "I'll try."
# - DO NOT use if payment is conditional on a credit or discount — use ACT007 for that.

# ### ACT005 — PartialPayment:
# - Use when customer commits to paying a specific amount LESS than the full balance. E.g., "I'll send $50 today."
# - ACT005 ALWAYS takes priority over ACT001 for partial amounts.

# ### ACT006 — DoubtfulReceivable — ALWAYS FIRE THIS FIRST:
# - Create an ACT006 entry whenever a customer says they CANNOT or WILL NOT make any payment.
# - Trigger phrases: "I won't be able to make that payment", "I cannot pay", "I refuse to pay", "I have financial issues and cannot pay."
# - IMPORTANT: Even if the customer LATER agrees to a callback or credit negotiation, you MUST still create an ACT006 entry for the initial refusal.
# - ACT006 coexists with other actions (ACT007, ACT002 etc.) that reflect what the customer was willing to do after the refusal.

# ### ACT007 — CreditRequest — ALWAYS FIRE FOR CONDITIONAL PAYMENT:
# - Create an ACT007 entry whenever a customer:
#   (a) Directly asks for a discount, credit, or reduction: "give me a 20% credit", "reduce the balance."
#   (b) Makes payment CONDITIONAL on receiving credit: "I'll pay IF you give me a discount", "I can pay only if you give me 20% credit."
# - IMPORTANT: Conditional payment statements ("I can make a payment if you give me 20% credit") ALWAYS trigger ACT007. Do not skip this.
# - Populate 'requested_amount' if a specific dollar amount is mentioned, and 'reason' with the customer's justification.

# ### ACT003 — Dispute:
# - Use when customer contests a charge they believe is wrong or for a service not delivered.
# - E.g., "I have a $200 dispute on the invoice."

# ### ACT002 — Escalation:
# - Use when customer requests or agrees to a callback from a senior executive, manager, or supervisor.
# - Populate target_resolution_date and due_by with the specific callback date/time if mentioned.

# ### ACT004 — DocumentCopy — SEPARATE FROM ACT008:
# - Use when customer REQUESTS A DOCUMENT TO BE SENT via email, post, or any channel.
# - E.g., "Can you send me that invoice to my email?", "Send me the receipt."
# - IMPORTANT: ACT004 is based on what the CUSTOMER REQUESTS, NOT whether the agent fulfilled it. Even if the agent says "I cannot send it", the customer's request MUST still be classified as ACT004.
# - IMPORTANT: If the customer first asks about their invoice number (ACT008) and then asks to have it emailed (ACT004), these are TWO SEPARATE actions — create one ACT008 AND one ACT004.
# - Always set delivery_channel to 'Email' unless stated otherwise.

# ### ACT008 — OtherCustomerRequest:
# - Use for any request NOT covered by ACT001–ACT007. Examples:
#   - Asking for invoice number or account details: "What is my invoice number?" → ACT008
#   - Address or email update requests
#   - Any general account inquiry
# - IMPORTANT: If customer asks "What is my invoice number?" AND THEN asks "Can you send it to my email?" — create TWO entries: ACT008 (inquiry) + ACT004 (document send).
# - Populate request_details with a full description of the request.

# ### General Rules:
# - Create a SEPARATE action entry for EACH distinct customer request. One action per object.
# - Always populate 'notes' with verbatim or close paraphrase from the transcript.
# - For ACT002: set escalation_level to 'L1' if not specified.
# - If no actions found, return an empty list.
# - Do NOT hallucinate data not present in the transcript."""

#     prompt = ChatPromptTemplate.from_messages([
#         ("system", system_prompt),
#         ("human", "Customer Name: {user_name}\nTranscript:\n{transcript}")
#     ])

#     chain = prompt | structured_llm

#     transcript = call_data.get("transcript", "")
#     if isinstance(transcript, (dict, list)):
#         transcript = json.dumps(transcript)

#     try:
#         token_cb = TokenUsageCallback()
#         result = await chain.ainvoke(
#             {
#                 "user_name": call_data.get("user_name", "Unknown"),
#                 "transcript": transcript
#             },
#             config={"callbacks": [token_cb]}
#         )

#         call_data['summary'] = result.summary
#         call_data['action_items'] = {
#             "user_action": result.user_action,
#             "agent_action_item": result.agent_action_item
#         }
#         call_data['categorization'] = result.category
#         call_data['actions'] = [action.to_strict_dict() for action in result.actions]
#         # gpt-4o pricing: $2.50/1M prompt tokens, $10.00/1M completion tokens
#         prompt_cost = (token_cb.prompt_tokens / 1_000_000) * 2.50
#         completion_cost = (token_cb.completion_tokens / 1_000_000) * 10.00
#         call_data['token_usage'] = {
#             "prompt_tokens": token_cb.prompt_tokens,
#             "completion_tokens": token_cb.completion_tokens,
#             "total_tokens": token_cb.total_tokens,
#             "estimated_cost_usd": round(prompt_cost + completion_cost, 6)
#         }

#     except Exception as e:
#         print(f"Error processing transcript: {e}")
#         call_data['summary'] = "Error extracting summary."
#         call_data['action_items'] = {"user_action": "", "agent_action_item": ""}
#         call_data['categorization'] = "Unknown"
#         call_data['actions'] = []

#     return call_data