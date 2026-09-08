import re
import json
from typing import Dict, Any, List, Optional


# -------------------------------------------------------------------
# SCENARIO KEYWORDS
# -------------------------------------------------------------------

SCENARIO_KEYWORDS: Dict[str, List[str]] = {
    "payment_due": [
        "past due",
        "overdue",
        "due payment",
        "payment due",
        "balance due",
        "outstanding",
        "outstanding balance",
        "pending payment",
        "unpaid",
        "collections",
        "make payment",
        "pay today",
        "clear the balance",
        "settle the amount",
    ],

    "email": [
        "invoice copy",
        "send invoice",
        "email invoice",
        "statement",
        "bill details",
        "billing details",
        "bank statement",
        "send email",
        "mail me",
        "email me",
        "copy of invoice",
        "resend invoice",
        "send statement",
        "send bill",
        "bill copy",
        "email address",
        "update email",
        "wrong email",
    ],

    "escalation": [
        "manager",
        "supervisor",
        "executive",
        "senior",
        "escalate",
        "escalation",
        "discount",
        "settlement",
        "human agent",
        "call back",
        "callback",
        "language issue",
        "not understanding",
        "speak to someone",
        "talk to manager",
        "complaint",
        "higher authority",
    ],

    "dispute": [
        "dispute",
        "incorrect bill",
        "wrong amount",
        "billing issue",
        "not correct",
        "charged wrongly",
        "amount incorrect",
        "invoice incorrect",
        "not my charge",
        "not valid",
        "wrong invoice",
        "incorrect amount",
        "overcharged",
        "invoice mismatch",
        "already paid",
        "payment already made",
    ],

    "partial_payment": [
        "partial payment",
        "part payment",
        "partial pay",
        "pay partially",
        "pay some",
        "can pay some",
        "pay some amount",
        "some amount",
        "some today",
        "remaining later",
        "pay half",
        "half payment",
        "half amount",
        "less than full",
        "cannot pay full",
        "can't pay full",
        "cant pay full",
        "not full amount",
        "pay in parts",
        "split payment",
        "installment",
        "instalment",
        "payment plan",
        "pay 10%",
        "pay 10 percent",
        "10%",
        "10 percent",
        "pay 50%",
        "pay 50 percent",
        "50%",
        "50 percent",
    ],

    "npr": [
        "refuses to pay",
        "refuse to pay",
        "will not pay",
        "won't pay",
        "wont pay",
        "don't want to pay",
        "dont want to pay",
        "does not want to pay",
        "cannot pay",
        "can't pay",
        "cant pay",
        "unable to pay",
        "no money",
        "financial problem",
        "financial issue",
        "job loss",
        "lost job",
        "salary issue",
        "not possible",
        "no payment",
        "no promise",
        "refusal",
    ],
}


# -------------------------------------------------------------------
# SCENARIO PROFILES
# -------------------------------------------------------------------

SCENARIO_PROFILES: Dict[str, Dict[str, Any]] = {
    "payment_due": {
        "scenario_label": "Payment Due / Past Due",
        "tone": "Firm, professional, respectful, and slightly urgent.",
        "agent_characteristics": [
            "Stay focused on resolving the overdue balance.",
            "Sound confident but not aggressive.",
            "Create urgency around resolving the payment today.",
            "Ask direct but polite payment questions.",
        ],
        "conversation_strategy": [
            "After verification, clearly state the balance and due date.",
            "Ask if the customer can make payment today.",
            "If they cannot pay today, ask for a specific payment date.",
            "If the date is more than 2-4 days away, ask for a partial payment within 48-72 hours.",
        ],
        "avoid": [
            "Do not sound casual about the overdue balance.",
            "Do not threaten legal action.",
            "Do not repeatedly interrupt the customer.",
        ],
        "sample_style": (
            "I understand. Since this balance is already past due, "
            "I'd like to help get this sorted as soon as possible. "
            "What amount would you be able to take care of today?"
        ),
    },

    "email": {
        "scenario_label": "Invoice / Email Request",
        "tone": "Helpful, clear, procedural, and service-oriented.",
        "agent_characteristics": [
            "Sound supportive and organized.",
            "Help the customer get the invoice or statement they need.",
            "Confirm available contact information carefully.",
            "Gently guide the conversation back to payment after addressing the request.",
        ],
        "conversation_strategy": [
            "Verify the customer first.",
            "If invoice number is available, provide it when asked.",
            "If email is known, confirm invoice can be sent to that email.",
            "If email is unknown or new, arrange executive follow-up.",
            "After handling the email request, ask when payment can be expected.",
        ],
        "avoid": [
            "Do not claim you updated email details if you cannot.",
            "Do not disclose invoice details before verification.",
            "Do not ignore the invoice request and push payment immediately.",
        ],
        "sample_style": (
            "Sure, I can help with that. Once I confirm I'm speaking with the right person, "
            "I can go over the invoice details I have and note that a copy needs to be sent."
        ),
    },

    "escalation": {
        "scenario_label": "Escalation / Supervisor / Executive Callback",
        "tone": "Calm, respectful, de-escalating, and solution-focused.",
        "agent_characteristics": [
            "Sound composed even if the customer is frustrated.",
            "Acknowledge the request without resistance.",
            "Avoid sounding defensive.",
            "Focus on arranging the right follow-up.",
        ],
        "conversation_strategy": [
            "Acknowledge the customer's request for a supervisor or executive.",
            "Ask for a specific callback date and time.",
            "Capture reason for escalation briefly.",
            "If appropriate, ask whether any partial payment is possible before callback.",
        ],
        "avoid": [
            "Do not argue about escalation.",
            "Do not say a manager is unavailable in a dismissive way.",
            "Do not pressure aggressively once escalation is requested.",
        ],
        "sample_style": (
            "I understand. I can arrange for an executive to follow up with you. "
            "What date and time would work best for that callback?"
        ),
    },

    "dispute": {
        "scenario_label": "Billing / Invoice Dispute",
        "tone": "Calm, investigative, patient, and non-defensive.",
        "agent_characteristics": [
            "Sound like you are trying to understand the issue.",
            "Do not argue about whether the customer is right or wrong.",
            "Ask clarifying questions.",
            "Separate disputed and undisputed amounts if possible.",
        ],
        "conversation_strategy": [
            "Acknowledge the dispute.",
            "Ask what specifically looks incorrect.",
            "Capture the disputed reason.",
            "Tell the customer the team will review it.",
            "Ask whether they can pay any undisputed portion.",
        ],
        "avoid": [
            "Do not insist the bill is correct.",
            "Do not demand full payment while the dispute is unresolved.",
            "Do not invent itemized details.",
        ],
        "sample_style": (
            "I hear you. Let me make sure I understand what looks incorrect. "
            "Is it the invoice amount, the service period, or a specific charge you're disputing?"
        ),
    },

    "partial_payment": {
        "scenario_label": "Partial Payment / Installment Request",
        "tone": "Collaborative, flexible, practical, and encouraging.",
        "agent_characteristics": [
            "Sound willing to work with the customer.",
            "Treat partial payment as progress.",
            "Ask for realistic numbers and dates.",
            "Guide toward a concrete commitment.",
        ],
        "conversation_strategy": [
            "Acknowledge that they cannot pay in full.",
            "Ask what amount they can pay within 48-72 hours.",
            "Ask for a specific payment date.",
            "For formal installment plans, arrange executive callback.",
        ],
        "avoid": [
            "Do not reject partial payment outright.",
            "Do not shame the customer.",
            "Do not create a formal installment plan yourself if policy requires executive callback.",
        ],
        "sample_style": (
            "I understand paying the full amount may not be possible right now. "
            "What would be a realistic partial amount you could manage in the next two or three days?"
        ),
    },

    "npr": {
        "scenario_label": "No Promise / Refusal / Cannot Pay",
        "tone": "Empathetic, calm, low-pressure, and recovery-focused.",
        "agent_characteristics": [
            "Do not sound confrontational.",
            "Explore the reason behind refusal or inability to pay.",
            "Look for the earliest realistic contribution.",
            "Escalate if the customer firmly refuses.",
        ],
        "conversation_strategy": [
            "Acknowledge the difficulty.",
            "Ask what is preventing payment.",
            "Ask for the earliest realistic date they could contribute.",
            "If they firmly refuse, arrange senior executive follow-up.",
        ],
        "avoid": [
            "Do not threaten.",
            "Do not repeatedly demand payment after firm refusal.",
            "Do not make the customer feel attacked.",
        ],
        "sample_style": (
            "I understand this may not be easy right now. "
            "Can you help me understand what is making payment difficult, "
            "and what the earliest realistic date might be for any contribution?"
        ),
    },

    "unknown": {
        "scenario_label": "Default Collections Conversation",
        "tone": "Professional, balanced, polite, and discovery-oriented.",
        "agent_characteristics": [
            "Start with normal verification.",
            "Discover the customer's situation.",
            "Adapt after the customer explains.",
        ],
        "conversation_strategy": [
            "Verify the customer.",
            "State the balance and due date.",
            "Ask whether they have reviewed the payment.",
            "Respond based on their answer.",
        ],
        "avoid": [
            "Do not assume a dispute, hardship, or refusal unless the customer says it.",
        ],
        "sample_style": (
            "I'm reaching out to help get this account sorted. "
            "Have you had a chance to look into the outstanding balance?"
        ),
    },
}


# -------------------------------------------------------------------
# ALIASES
# -------------------------------------------------------------------

SCENARIO_ALIASES = {
    "Escalation": "escalation",
    "escalation": "escalation",

    "Partial Payment": "partial_payment",
    "partial payment": "partial_payment",
    "partial_payment": "partial_payment",

    "Payment Due": "payment_due",
    "payment due": "payment_due",
    "payment_due": "payment_due",
    "past_due": "payment_due",

    "Email": "email",
    "email": "email",
    "invoice": "email",
    "invoice_request": "email",

    "Dispute": "dispute",
    "dispute": "dispute",
    "billing_dispute": "dispute",

    "NPR": "npr",
    "npr": "npr",
    "refusal": "npr",
    "no_payment_refusal": "npr",
    "cannot_pay": "npr",

    "Unknown": "unknown",
    "unknown": "unknown",
    None: "unknown",
}


# -------------------------------------------------------------------
# TEXT NORMALIZATION
# -------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Normalizes text for keyword matching.
    Includes common typo and wording corrections.
    """

    text = text or ""
    text = str(text).lower()

    replacements = {
        # Common typo fixes
        "partail": "partial",
        "parital": "partial",
        "partiall": "partial",
        "partil": "partial",

        # Payment wording normalization
        "cant pay": "can't pay",
        "dont want": "don't want",
        "wont pay": "won't pay",

        # Installment variants
        "instalment": "installment",

        # Email typo variants
        "emai ": "email ",
        "e-mail": "email",
    }

    for wrong, right in replacements.items():
        text = text.replace(wrong, right)

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def flatten_value_to_text(value: Any) -> List:
    """
    Recursively converts strings/lists/dicts into text parts.
    Useful for call_history and nested call_data.
    """

    parts: List[str] = []

    if value is None:
        return parts

    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            parts.append(cleaned)
        return parts

    if isinstance(value, (int, float, bool)):
        parts.append(str(value))
        return parts

    if isinstance(value, list):
        for item in value:
            parts.extend(flatten_value_to_text(item))
        return parts

    if isinstance(value, dict):
        for item_value in value.values():
            parts.extend(flatten_value_to_text(item_value))
        return parts

    parts.append(str(value))
    return parts


def collect_call_text(user_data: Dict[str, Any]) -> str:
    """
    Combines call_history, notes, call_data, previous disposition,
    and other useful fields into one classification text.

    Supports both:
      - call_history
      - call_histroy
    """

    if not user_data:
        return ""

    text_parts: List[str] = []

    possible_fields = [
        "scenario_text",
        "call_reason",
        "reason",
        "description",
        "remarks",

        "call_data",
        "notes",
        "customer_notes",
        "collector_notes",
        "invoice_notes",
        "agent_notes",

        "call_notes",
        "call_history",
        "call_histroy",
        "previous_call_history",
        "last_call_summary",

        "customer_intent",
        "disposition",
        "previous_disposition",
        "last_disposition",
    ]

    for field in possible_fields:
        if field in user_data:
            text_parts.extend(flatten_value_to_text(user_data.get(field)))

    return normalize_text(" ".join(text_parts))


# -------------------------------------------------------------------
# SCENARIO NORMALIZATION
# -------------------------------------------------------------------

def normalize_scenario_name(scenario: Optional[str]) -> str:
    """
    Converts scenario aliases and messy labels to canonical keys.
    """

    if scenario in SCENARIO_ALIASES:
        return SCENARIO_ALIASES[scenario]

    scenario = str(scenario or "unknown").strip().lower()
    scenario = scenario.replace(" ", "_").replace("-", "_")

    return SCENARIO_ALIASES.get(scenario, scenario)


def get_scenario_profile(scenario: Optional[str]) -> Dict[str, Any]:
    """
    Returns the scenario profile. Unknown scenarios fall back to unknown.
    """

    normalized = normalize_scenario_name(scenario)
    return SCENARIO_PROFILES.get(normalized, SCENARIO_PROFILES["unknown"])


# -------------------------------------------------------------------
# SPECIAL RULE MATCHERS
# -------------------------------------------------------------------

def apply_special_rules(
    normalized_text: str,
    scenario_scores: Dict[str, int],
    scenario_matches: Dict[str, List[str]],
) -> None:
    """
    Adds regex/special matches that are difficult to capture with static keywords.
    Mutates scenario_scores and scenario_matches.
    """

    # Detect percentage-based partial payments:
    # examples:
    # partial payment of 50%
    # pay 50%
    # can pay 40 percent
    # part payment 30%
    percentage_match = re.search(
        r"\b(?:pay|payment|paid|amount|partial|part|some|half)?\s*(?:of\s*)?\d{1,3}\s*%|\b\d{1,3}\s*percent\b",
        normalized_text,
    )

    partial_context_words = [
        "partial",
        "part",
        "some",
        "half",
        "payment",
        "pay",
        "amount",
        "remaining",
        "split",
    ]

    if percentage_match and any(word in normalized_text for word in partial_context_words):
        scenario_scores["partial_payment"] = scenario_scores.get("partial_payment", 0) + 2
        scenario_matches.setdefault("partial_payment", []).append(
            percentage_match.group(0).strip()
        )

    # Detect "X percent payment" patterns
    percent_payment_match = re.search(
        r"\b\d{1,3}\s*(?:%|percent)\s*(?:payment|pay|amount)\b",
        normalized_text,
    )

    if percent_payment_match:
        scenario_scores["partial_payment"] = scenario_scores.get("partial_payment", 0) + 2
        scenario_matches.setdefault("partial_payment", []).append(
            percent_payment_match.group(0).strip()
        )

    # Detect generic "pay remaining later"
    if "remaining" in normalized_text and "later" in normalized_text:
        scenario_scores["partial_payment"] = scenario_scores.get("partial_payment", 0) + 1
        scenario_matches.setdefault("partial_payment", []).append("remaining later")

    # Detect already-paid as dispute/payment verification type
    if any(x in normalized_text for x in ["already paid", "payment already made", "paid already"]):
        scenario_scores["dispute"] = scenario_scores.get("dispute", 0) + 1
        scenario_matches.setdefault("dispute", []).append("already paid/payment already made")


# -------------------------------------------------------------------
# CLASSIFICATION
# -------------------------------------------------------------------

def classify_scenario_from_text(text: str) -> Dict[str, Any]:
    """
    Simple keyword-based scenario classifier.

    Returns:
        {
            "classification": "payment_due" | "email" | ... | "unknown",
            "scenario_label": str,
            "score": float,
            "matched_keywords": list[str],
            "matched_scenarios": dict,
            "classification_text_sample": str
        }
    """

    normalized_text = normalize_text(text)

    if not normalized_text:
        unknown_profile = get_scenario_profile("unknown")
        return {
            "classification": "unknown",
            "scenario_label": unknown_profile["scenario_label"],
            "score": 0.0,
            "matched_keywords": [],
            "matched_scenarios": {},
            "classification_text_sample": "",
        }

    scenario_scores: Dict[str, int] = {}
    scenario_matches: Dict[str, List[str]] = {}

    for scenario, keywords in SCENARIO_KEYWORDS.items():
        canonical_scenario = normalize_scenario_name(scenario)
        matched: List[str] = []

        for keyword in keywords:
            keyword_normalized = normalize_text(keyword)

            if keyword_normalized and keyword_normalized in normalized_text:
                matched.append(keyword)

        if matched:
            scenario_scores[canonical_scenario] = scenario_scores.get(canonical_scenario, 0) + len(matched)
            scenario_matches.setdefault(canonical_scenario, []).extend(matched)

    # Apply regex/special matching after keyword matching
    apply_special_rules(
        normalized_text=normalized_text,
        scenario_scores=scenario_scores,
        scenario_matches=scenario_matches,
    )

    if not scenario_scores:
        unknown_profile = get_scenario_profile("unknown")
        return {
            "classification": "unknown",
            "scenario_label": unknown_profile["scenario_label"],
            "score": 0.0,
            "matched_keywords": [],
            "matched_scenarios": {},
            "classification_text_sample": normalized_text[:500],
        }

    best_scenario = max(
        scenario_scores,
        key=lambda scenario: (
            scenario_scores[scenario],
            len(scenario_matches.get(scenario, [])),
        ),
    )

    matched_keywords = scenario_matches.get(best_scenario, [])

    # Remove duplicate matched keywords while preserving order
    matched_keywords = list(dict.fromkeys(matched_keywords))

    keyword_count = len(SCENARIO_KEYWORDS.get(best_scenario, []))

    score = round(
        min(1.0, scenario_scores[best_scenario] / max(keyword_count, 1)),
        4,
    )

    profile = get_scenario_profile(best_scenario)

    return {
        "classification": best_scenario,
        "scenario_label": profile["scenario_label"],
        "score": score,
        "matched_keywords": matched_keywords,
        "matched_scenarios": scenario_matches,
        "classification_text_sample": normalized_text[:500],
    }


def classify_scenario_from_user_data(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classifies scenario using user_data.

    Priority:
    1. If user_data["scenario"] is already present, respect it.
    2. Otherwise classify using call_history/call_histroy/notes/call_data/etc.
    """

    if not user_data:
        return classify_scenario_from_text("")

    if user_data.get("scenario"):
        scenario = normalize_scenario_name(user_data.get("scenario"))
        profile = get_scenario_profile(scenario)

        return {
            "classification": scenario,
            "scenario_label": profile["scenario_label"],
            "score": float(user_data.get("scenario_score", 1.0) or 1.0),
            "matched_keywords": user_data.get("matched_keywords", []),
            "matched_scenarios": {
                scenario: user_data.get("matched_keywords", []),
            },
            "classification_text_sample": collect_call_text(user_data)[:500],
            "source": "provided_scenario",
        }

    call_text = collect_call_text(user_data)
    result = classify_scenario_from_text(call_text)
    result["source"] = "keyword_matching"

    return result


def enrich_user_data_with_scenario(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classifies scenario and writes result back into user_data.

    Mutates and returns user_data.
    """

    if user_data is None:
        user_data = {}

    result = classify_scenario_from_user_data(user_data)

    user_data["scenario"] = result.get("classification", "unknown")
    user_data["scenario_label"] = result.get("scenario_label", "Default Collections Conversation")
    user_data["scenario_score"] = result.get("score", 0.0)
    user_data["matched_keywords"] = result.get("matched_keywords", [])
    user_data["matched_scenarios"] = result.get("matched_scenarios", {})
    user_data["scenario_classification_source"] = result.get("source", "keyword_matching")
    user_data["scenario_classification_text_sample"] = result.get("classification_text_sample", "")

    return user_data


# -------------------------------------------------------------------
# PROMPT FORMATTER
# -------------------------------------------------------------------

def format_scenario_profile_for_prompt(user_data: Dict[str, Any]) -> str:
    """
    Returns a prompt-ready behavior profile block.

    If user_data["scenario"] exists, uses it.
    Otherwise performs keyword classification from call_history/call_histroy/notes/etc.
    """

    user_data = enrich_user_data_with_scenario(user_data)

    scenario = user_data.get("scenario", "unknown")
    profile = get_scenario_profile(scenario)

    def bullet(items: List[str]) -> str:
        return "\n".join(f"- {item}" for item in items)

    return f"""
# Scenario Behavior Profile

The classifier identified this call as:

- Scenario: {profile["scenario_label"]}
- Scenario Key: {scenario}
- Scenario Score: {user_data.get("scenario_score", 0)}
- Classification Source: {user_data.get("scenario_classification_source", "unknown")}
- Matched Keywords: {user_data.get("matched_keywords", [])}

You must adjust your tone and behavior according to this scenario.

## Required Tone
{profile["tone"]}

## Agent Characteristics
{bullet(profile["agent_characteristics"])}

## Conversation Strategy
{bullet(profile["conversation_strategy"])}

## Avoid
{bullet(profile["avoid"])}

## Example Style
"{profile["sample_style"]}"
"""


# -------------------------------------------------------------------
# OPTIONAL TEST
# -------------------------------------------------------------------

if __name__ == "__main__":
    sample_user_data = {
        "case_id": "09d8c94b-Collections-Mervin-59",
        "call_id": "09d8c94b-HARI-Collections-Mervin-59",
        "customer_number": "7f1e5e9d",
        "customer_name": "mervin Thomas",
        "billing_address": "6th street ,20th cross",
        "contact_name": "MERVIN",
        "phone_number": "+919980979624",
        "email_address": "mervin.t@capgemini.com",
        "preferred_language": "en",
        "time_zone": "IST",
        "multiple_invoice": False,
        "call_type": 2,
        "attempt_no": 1,
        "call_data": "partail payment of 50%",
        "invoice_details": [
            {
                "invoice_number": "INV-SCN-006",
                "outstanding_balance": "1220.00",
                "overdue_status": "Open",
                "due_date": "2026-05-11T00:00:00",
                "days_past_due": 1,
                "purchase_order_num": "string",
                "order_date": None,
                "open_ptp": None,
                "open_ptp_date": None,
                "broken_ptp_flg": None,
                "broken_ptp_amount": "0.0",
                "broken_ptp_date": None,
                "open_dispute": None,
                "closed_dispute_status": "string",
                "issued_credits": "1000.00",
            }
        ],
    }

    result = classify_scenario_from_user_data(sample_user_data)
    print("CLASSIFICATION RESULT")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    enrich_user_data_with_scenario(sample_user_data)
    print("\nENRICHED USER DATA")
    print(json.dumps(sample_user_data, indent=2, ensure_ascii=False))

    print("\nPROMPT BLOCK")
    print(format_scenario_profile_for_prompt(sample_user_data))