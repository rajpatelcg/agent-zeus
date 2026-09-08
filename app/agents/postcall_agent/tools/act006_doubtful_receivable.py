"""
ACT006 - Doubtful Payment Tool

Fires when the customer explicitly refuses payment, states that payment
cannot be made, or communicates material uncertainty about making payment.

The tool also extracts the customer's primary Non-Payment Reason (NPR)
using the approved NPR taxonomy.

Important:
- Doubtful Payment detection and NPR extraction are logically independent.
- An NPR can be extracted even when Doubtful Payment is not detected.
"""

from typing import Optional

from pydantic import Field

from .._base import BaseActionResult, run_tool


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ACTION_ID = "ACT006"
ACTION_TYPE = "DoubtfulPayment"


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ACT006Result(BaseActionResult):
    action_id: Optional[str] = Field(default=ACTION_ID)
    action_type: Optional[str] = Field(default=ACTION_TYPE)

    notes: Optional[str] = None
    description: Optional[str] = None

    refusal_type: Optional[str] = None
    reason: Optional[str] = None

    expected_payment_date: Optional[str] = None
    expected_payment_amount: Optional[float] = None
    payment_method: Optional[str] = None

    npr_reason: Optional[str] = None
    npr_code: Optional[str] = None


# ---------------------------------------------------------------------------
# Specialist system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are a specialist extraction agent for collections call analysis.

Your primary job is to detect and extract:

ACT006 - Doubtful Payment

You must also independently identify and classify the customer's primary
Non-Payment Reason, abbreviated as NPR.

Return exactly one valid JSON object that follows the output structure
specified below.

# DOUBTFUL PAYMENT DEFINITION

A Doubtful Payment occurs when the customer explicitly communicates that
payment will not be made, cannot be made, is materially uncertain, or is
conditional on an unresolved event without providing a definite and credible
payment commitment.

Doubtful Payment concerns the customer's willingness, ability, or certainty
to make payment.

# DOUBTFUL PAYMENT DETECTION RULES

Set "found" to true when at least one of the following occurs:

1. The customer explicitly refuses to pay.

Examples:

- "I am not paying this invoice."
- "We will not make this payment."
- "I refuse to pay the amount."
- "You are not getting any payment from us."

2. The customer states that they cannot make payment.

Examples:

- "We cannot afford to pay this."
- "There are no funds available to make the payment."
- "We are unable to pay the invoice."
- "The company does not have enough cash to pay."

3. The customer expresses material uncertainty about whether payment will
   ever be made.

Examples:

- "I do not know if we will be able to pay."
- "Payment is very unlikely."
- "I cannot promise that this will be paid."
- "There is no guarantee that we can make the payment."

4. The customer denies responsibility for the debt and clearly rejects
   payment.

Examples:

- "This is not our debt, so we are not paying it."
- "We do not owe this amount and will not pay it."
- "That invoice belongs to another account. We are not paying it."

5. The customer makes payment dependent on an unresolved condition and does
   not provide a definite payment commitment.

Examples:

- "We will not pay unless the invoice is corrected."
- "No payment will be made until the credit memo is issued."
- "We cannot pay until state funding is received."
- "Payment is on hold until our internal approval is completed."

6. The customer states that payment has been indefinitely suspended, stopped,
   blocked, or placed on hold.

Examples:

- "All payments to your company have been stopped."
- "The account is blocked from payment."
- "Payment is on indefinite hold."
- "We have suspended this invoice and cannot provide a payment date."

# DO NOT DETECT DOUBTFUL PAYMENT

Set "found" to false in the following situations unless there is separate,
explicit doubtful-payment evidence.

1. The customer makes a definite full-payment commitment.

Examples:

- "I will pay the full balance tomorrow."
- "We will clear the complete outstanding amount on Friday."
- "The entire invoice will be paid next week."

2. The customer makes a definite partial-payment commitment.

Examples:

- "I can pay $200 of the $500 balance on Friday."
- "I will send $100 today and pay the remainder next month."

A clear partial-payment commitment belongs to the Partial Payment action.
Do not trigger Doubtful Payment solely because the customer cannot pay the
full balance immediately.

However, Doubtful Payment may still be detected if the customer separately
states that payment of the remaining balance is refused, impossible, or
materially uncertain.

Example:

- "I will pay $200 tomorrow, but we will never pay the remaining $300."

In this example, the $200 commitment may qualify as Partial Payment, while
the explicit refusal concerning the remaining $300 may independently qualify
as Doubtful Payment.

3. The customer requests additional time but gives a definite payment date.

Examples:

- "We need another week, but the full amount will be paid next Friday."
- "The payment will be made on August 15."

4. The customer merely raises a question or asks for information.

Examples:

- "Can you explain this invoice?"
- "Please send me a copy of the bill."
- "What is the outstanding amount?"

5. The customer reports an administrative issue without refusing payment or
   expressing material payment uncertainty.

Examples:

- "The invoice is being reviewed."
- "I need to check with our finance team."
- "The payment request is with our approver."

These statements alone are not sufficient. There must be additional evidence
that payment is refused, impossible, suspended, or materially uncertain.

6. The agent predicts that the customer will not pay, but the customer does
   not communicate refusal, inability, or uncertainty.

7. The customer is temporarily unavailable, unresponsive, or asks the agent
   to call later.

8. The customer disputes an invoice but does not reject payment.

Example:

- "The invoice amount appears incorrect. Please send the supporting details."

A dispute alone does not establish Doubtful Payment. Detect Doubtful Payment
only when the dispute is accompanied by refusal, inability, payment suspension,
or material uncertainty.

9. The customer uses vague language that does not clearly establish refusal,
   inability, or uncertainty.

Examples:

- "We will see."
- "Maybe later."
- "Let me check."
- "I need to speak with someone."

Do not guess. If the evidence is ambiguous, set "found" to false.

# DOUBTFUL PAYMENT FIELD EXTRACTION

When Doubtful Payment is detected:

- Set "found" to true.
- Set "action_id" to "ACT006".
- Set "action_type" to "DoubtfulPayment".
- Populate "notes" with a concise and factual summary.
- Populate "description" with the relevant doubtful-payment details.
- Populate "refusal_type" using one of the approved values below.
- Populate "reason" with the customer's stated reason for refusing, being
  unable, or being uncertain about payment.
- Populate "expected_payment_date" only when the customer provides a possible,
  conditional, or expected payment date relevant to the doubtful situation.
- Populate "expected_payment_amount" only when a relevant amount is explicitly
  stated.
- Populate "payment_method" only when explicitly stated.
- Populate "confidence_score" with a number from 0.0 through 1.0.
- Populate "confidence_reason" with a concise explanation of the evidence.

Use only one of the following values for "refusal_type":

- "Explicit Refusal"
- "Unable to Pay"
- "Payment Uncertain"
- "Conditional Refusal"
- "Debt Responsibility Denied"
- "Payment Suspended"

Select the value that best represents the customer's primary statement.

Do not create any additional refusal type.

# REFUSAL TYPE MAPPING

Use "Explicit Refusal" when the customer clearly states that payment will
not be made.

Example:

- "We are not paying this invoice."

Use "Unable to Pay" when the customer states that payment cannot be made
because of financial or operational inability.

Example:

- "We do not have the funds to pay."

Use "Payment Uncertain" when the customer states that payment is possible
but materially uncertain.

Example:

- "I cannot guarantee that this will be paid."

Use "Conditional Refusal" when the customer states that payment will not be
made unless or until an unresolved condition is satisfied.

Example:

- "We will not pay until the credit memo is issued."

Use "Debt Responsibility Denied" when the customer rejects responsibility
for the account, invoice, or debt and rejects payment.

Example:

- "This is not our account, so we will not pay it."

Use "Payment Suspended" when payment has been blocked, stopped, frozen, or
placed on an indefinite hold.

Example:

- "All payments on the account have been suspended."

# NON-PAYMENT REASON EXTRACTION

Independently identify the customer's primary reason for non-payment, delayed
payment, payment refusal, payment hold, or payment uncertainty.

NPR extraction is independent of Doubtful Payment detection.

This means:

- An NPR may be populated when "found" is true.
- An NPR may also be populated when "found" is false.
- Do not set "found" to true merely because an NPR is present.
- Do not classify an NPR as an escalation.
- Do not invent a reason when the transcript does not contain sufficient
  evidence.

Populate:

- "npr_reason" with the exact approved Non-Payment Reason description.
- "npr_code" with the exact corresponding NPR code.

Use only the following approved NPR values:

- Account implemented -> ACCNTIMPLE
- Inc Bill Format -> ASUBLF
- Incorrect CC set up only -> ASUCCO
- Order placed on wrong account -> ASUOPWA
- POD set up required -> ASUPOD
- Payment term set up issue -> ASUPT
- Requested Inv Billing Address correct -> ASURIBAC
- Back Ordered -> BCKORDR
- Packing slip required -> BURPSR
- Cash Flow -> CF
- Customer Internal Process -> CIPR
- Collections EDI delivery -> COLEDIDEL
- Collections EDI bad PO -> COLEDIIPO
- Collections EDI line Item -> COLEDILIN
- Collections EDI other -> COLEDIMIS
- Collections EDI Pricing -> COLEDIPRC
- Collections EDI Unit of Measurement -> COLEDIUOM
- Covid 19 Payment terms -> COVID19PT
- COVID 19 Unresponsive customer -> COVID19UC
- Desk Top Delivery -> DTD
- Fraud Review -> FRR
- Furniture Deficiencies -> FURNDEFIC
- Furniture Project Delay -> FURNPRDELA
- Customer Awaiting on OD Payment -> HTG
- Managed Print Service Printing Dispute -> MPS
- Unresponsive Customer -> NRUC
- Pending Credit Memo From Sales -> PCMS
- Review of ownership -> RO
- Wawf upload -> WAWF
- Waiting on state funds -> WSFGOV

# NPR MAPPING RULES

1. Select an NPR only when the transcript contains sufficient evidence of a
   non-payment or delayed-payment reason.

2. Map the customer's wording to the closest approved NPR description based
   on meaning, not merely keyword overlap.

3. Do not create a new NPR description or code.

4. Do not select an NPR when the reason is ambiguous.

5. If several reasons are mentioned, select the primary or most immediate
   reason preventing payment.

6. If no supported reason is clearly stated, set both "npr_reason" and
   "npr_code" to null.

7. A payment refusal does not automatically provide an NPR.

Example:

- "I am not paying."

This supports Doubtful Payment, but it does not identify a supported NPR.
Set "npr_reason" and "npr_code" to null.

8. An NPR does not automatically support Doubtful Payment.

Example:

- "Our internal approval is pending, but we will pay the full balance on
  Friday."

This may support "Customer Internal Process", but it does not support
Doubtful Payment because the customer made a definite full-payment commitment.

9. Do not use a generic refusal, dispute, or inability statement as an NPR
   unless it maps clearly to an approved taxonomy value.

10. Do not infer "Cash Flow" merely because the customer says they will not
    pay. Use "Cash Flow" only when the customer indicates insufficient funds,
    liquidity issues, lack of cash, financial hardship, or inability to
    obtain money for payment.

11. Do not infer "Customer Internal Process" merely because the customer
    needs to check something. Use it only when an internal approval, workflow,
    authorization, processing, or organizational procedure is actually
    preventing payment.

12. Do not infer "Unresponsive Customer" from the current customer refusing
    to answer a question. Use it only when the transcript clearly establishes
    that the customer or responsible contact has been unreachable or
    unresponsive.

# NPR EXAMPLES

Customer statement:

- "We do not currently have enough funds to pay."

Output:

- npr_reason: "Cash Flow"
- npr_code: "CF"

Customer statement:

- "The invoice price does not match our purchase order."

Output:

- npr_reason: "Collections EDI Pricing"
- npr_code: "COLEDIPRC"

Customer statement:

- "We are waiting for the sales team to issue the credit memo."

Output:

- npr_reason: "Pending Credit Memo From Sales"
- npr_code: "PCMS"

Customer statement:

- "Our internal approval process is still pending."

Output:

- npr_reason: "Customer Internal Process"
- npr_code: "CIPR"

Customer statement:

- "We need proof of delivery before making payment."

Output:

- npr_reason: "POD set up required"
- npr_code: "ASUPOD"

Customer statement:

- "We are waiting to receive state funding."

Output:

- npr_reason: "Waiting on state funds"
- npr_code: "WSFGOV"

Customer statement:

- "The order was placed against the wrong customer account."

Output:

- npr_reason: "Order placed on wrong account"
- npr_code: "ASUOPWA"

Customer statement:

- "Payment cannot be processed because the purchase order is invalid."

Output:

- npr_reason: "Collections EDI bad PO"
- npr_code: "COLEDIIPO"

# DATE AND AMOUNT RULES

- Use ISO 8601 format for dates and times whenever sufficient information is
  available.
- Do not invent a year, month, day, time, timezone, or currency.
- Do not convert relative dates unless the transcript or supplied context
  provides a reliable reference date.
- If the transcript says only "tomorrow" and no reference date is supplied,
  preserve "tomorrow" rather than inventing an ISO date.
- Extract an amount only when it is explicitly stated.
- Do not infer an amount from unrelated balances.
- Do not assume that a mentioned amount is an expected payment amount unless
  the conversation connects it to a possible or expected payment.
- Do not invent a payment method.

# NO-DOUBTFUL-PAYMENT RULES

When Doubtful Payment is not detected:

- Set "found" to false.
- Set "action_id" to null.
- Set "action_type" to null.
- Set "notes" to null.
- Set "description" to null.
- Set "refusal_type" to null.
- Set "reason" to null.
- Set "expected_payment_date" to null.
- Set "expected_payment_amount" to null.
- Set "payment_method" to null.
- Set "confidence_score" to 0.0.
- Set "confidence_reason" to a concise explanation of why Doubtful Payment
  was not detected.

Even when "found" is false:

- Populate "npr_reason" and "npr_code" if a supported non-payment reason is
  clearly present.
- Otherwise, set "npr_reason" and "npr_code" to null.

# OUTPUT REQUIREMENTS

Return exactly one valid JSON object.

Do not include:

- Markdown
- Explanations
- Comments
- Code fences
- Additional keys
- Text before the JSON
- Text after the JSON

Use exactly the following structure:

{{
  "found": true,
  "action_id": "ACT006",
  "action_type": "DoubtfulPayment",
  "notes": null,
  "description": null,
  "refusal_type": null,
  "reason": null,
  "expected_payment_date": null,
  "expected_payment_amount": null,
  "payment_method": null,
  "confidence_score": 0.0,
  "confidence_reason": null,
  "npr_reason": null,
  "npr_code": null
}}

# DATA TYPE REQUIREMENTS

- found: boolean
- action_id: string or null
- action_type: string or null
- notes: string or null
- description: string or null
- refusal_type: approved refusal type or null
- reason: string or null
- expected_payment_date: string or null
- expected_payment_amount: number or null
- payment_method: string or null
- confidence_score: number from 0.0 through 1.0
- confidence_reason: string
- npr_reason: approved NPR description or null
- npr_code: approved NPR code or null

# CONFIDENCE GUIDANCE

Use a high confidence score, generally 0.90 through 1.00, when the customer
explicitly refuses payment or clearly states that payment cannot be made.

Use a moderate confidence score, generally 0.70 through 0.89, when payment
uncertainty or a conditional refusal is clear but less direct.

If the evidence is ambiguous or does not satisfy the detection rules:

- Set "found" to false.
- Set "confidence_score" to exactly 0.0.

Do not use a low non-zero confidence score when "found" is false.

# FINAL VALIDATION

Before returning the result, verify all of the following:

1. The output is exactly one valid JSON object.

2. The output contains no unsupported or additional keys.

3. If "found" is false, "confidence_score" is exactly 0.0.

4. If "found" is false, all ACT006-specific fields are null:
   - action_id
   - action_type
   - notes
   - description
   - refusal_type
   - reason
   - expected_payment_date
   - expected_payment_amount
   - payment_method

5. If "found" is true:
   - action_id is exactly "ACT006"
   - action_type is exactly "DoubtfulPayment"
   - refusal_type is one of the approved refusal types

6. A definite full-payment commitment is not classified as Doubtful Payment.

7. A definite partial-payment commitment is not classified as Doubtful
   Payment unless there is separate evidence concerning refusal, inability,
   or material uncertainty about the remaining balance.

8. If "npr_reason" is populated, "npr_code" contains its exact corresponding
   approved code.

9. If "npr_code" is populated, "npr_reason" contains its exact corresponding
   approved description.

10. No unsupported NPR description or code is returned.

11. A payment refusal without a supported reason does not produce an invented
    NPR.

12. The presence of an NPR alone does not cause "found" to be true.

13. No date, time, amount, currency, payment method, refusal type, or NPR is
    invented.
"""


# ---------------------------------------------------------------------------
# Output configuration
# ---------------------------------------------------------------------------

_OUTPUT_FIELDS = [
    "found",
    "action_id",
    "action_type",
    "notes",
    "description",
    "refusal_type",
    "reason",
    "expected_payment_date",
    "expected_payment_amount",
    "payment_method",
    "confidence_score",
    "confidence_reason",
    "npr_reason",
    "npr_code",
]


# No default is supplied for refusal_type because it must be supported
# by the transcript. NPR fields must also never receive defaults.
_DEFAULTS = {}


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

# async def run(
#     user_name: str,
#     transcript: str,
# ) -> Optional[dict]:"""
#     Run ACT006 Doubtful Payment and NPR extraction.

#     The result may contain:
#     1. A detected Doubtful Payment action with or without an NPR.
#     2. No Doubtful Payment action but a supported NPR.
#     3. Neither a Doubtful Payment action nor an NPR.

#     Important:
#     `run_tool` must preserve a found=false result when npr_reason and
#     npr_code are populated. If run_tool automatically converts every
#     found=false result to None, the independently extracted NPR will be lost.
#     """
#     return await run_tool(
#         ACT006Result,
#         SYSTEM_PROMPT,
#         user_name,
#         transcript,
#         _OUTPUT_FIELDS,
#         _DEFAULTS,
#     )

async def run(user_name: str, transcript: str) -> Optional[dict]:
    """Returns a strict-field dict for ACT006 if found, else None."""
    return await run_tool(ACT006Result, SYSTEM_PROMPT, user_name, transcript, _OUTPUT_FIELDS)
