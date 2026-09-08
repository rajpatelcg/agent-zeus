import os
import base64
import logging
from pathlib import Path
from markupsafe import escape
import requests

logger = logging.getLogger(__name__)

import os
from dotenv import load_dotenv
from typing import Optional
load_dotenv()

def get_post_call_email(
    customer_name: str,
    phone_number: str,
    outstanding_balance: float,
    call_status: bool = True,
    call_date: str = "",
    aging_details: str = "",
    customer_feedback: str = "",
    dispute_details: str = "",
    additional_details: str = "",
    call_summary: str = "",
    action_items: Optional[list] = None,
    document_types: Optional[list] = None,
    sender_name: str = "",
) -> str:
    """
    Generate an HTML post-call email.

    call_status=True:
        Customer was reached.

    call_status=False:
        Customer was not reached.

    Supported document types:
        invoice_copy
        credit_memo
        soa
    """

    action_items = action_items or []
    document_types = document_types or []

    # Convert input values to safe HTML text
    customer_name = escape(str(customer_name or "Customer"))
    phone_number = escape(str(phone_number or "the provided number"))
    sender_name = escape(str(sender_name or ""))
    call_date = escape(str(call_date or ""))
    aging_details = escape(str(aging_details or "N/A"))
    customer_feedback = escape(str(customer_feedback or "N/A"))
    dispute_details = escape(str(dispute_details or "None"))
    additional_details = escape(str(additional_details or "None"))
    call_summary = escape(str(call_summary or ""))

    # Format outstanding balance
    try:
        balance = float(
            str(outstanding_balance or 0)
            .replace("$", "")
            .replace(",", "")
            .strip()
        )
    except (ValueError, TypeError):
        balance = 0.0

    formatted_balance = f"${balance:,.2f}"

    # Opening section based on call status
    if call_status:
        opening_section = f"""
        <p>
            Thank you for taking my call at {phone_number}.
            Below is a summary of the key points discussed regarding
            the outstanding balance on your account.
        </p>
        """
    else:
        opening_section = f"""
        <p>
            I attempted to reach you at {phone_number} to discuss the
            outstanding balance on your account, but unfortunately,
            we were unable to connect.
        </p>
        """

    # Account details
    details = []

    if call_date:
        details.append(
            f"<li><strong>Call date:</strong> {call_date}</li>"
        )

    details.append(
        f"<li><strong>Outstanding balance:</strong> "
        f"{formatted_balance}</li>"
    )

    if aging_details and aging_details != "N/A":
        details.append(
            f"<li><strong>Aging details:</strong> "
            f"{aging_details}</li>"
        )

    # Add discussion details only for a successful call
    if call_status:
        details.append(
            f"<li><strong>Customer feedback:</strong> "
            f"{customer_feedback}</li>"
        )

        details.append(
            f"<li><strong>Disputes or issues:</strong> "
            f"{dispute_details}</li>"
        )

        details.append(
            f"<li><strong>Risks or potential delays:</strong> "
            f"{additional_details}</li>"
        )

    details_section = f"""
    <h3>Account Details</h3>
    <ul>
        {''.join(details)}
    </ul>
    """

    # Optional call summary
    summary_section = ""

    if call_summary:
        summary_section = f"""
        <h3>Call Summary</h3>
        <p style="white-space: pre-line;">
            {call_summary}
        </p>
        """

    # Build action items table
    action_section = ""

    if action_items:
        rows = ""

        for item in action_items:
            if isinstance(item, dict):
                action = escape(str(item.get("action", "")))
                owner = escape(str(item.get("owner", "")))
                due_date = escape(str(item.get("due_date", "")))
            else:
                action = escape(str(item))
                owner = ""
                due_date = ""

            rows += f"""
            <tr>
                <td style="border:1px solid #ddd;padding:8px;">
                    {action}
                </td>
                <td style="border:1px solid #ddd;padding:8px;">
                    {owner}
                </td>
                <td style="border:1px solid #ddd;padding:8px;">
                    {due_date}
                </td>
            </tr>
            """

        action_section = f"""
        <h3>Agreed Actions</h3>

        <table
            style="
                border-collapse:collapse;
                width:100%;
                margin-bottom:16px;
            "
        >
            <thead>
                <tr style="background-color:#f2f2f2;">
                    <th
                        style="
                            border:1px solid #ddd;
                            padding:8px;
                            text-align:left;
                        "
                    >
                        Action
                    </th>

                    <th
                        style="
                            border:1px solid #ddd;
                            padding:8px;
                            text-align:left;
                        "
                    >
                        Owner
                    </th>

                    <th
                        style="
                            border:1px solid #ddd;
                            padding:8px;
                            text-align:left;
                        "
                    >
                        Due Date
                    </th>
                </tr>
            </thead>

            <tbody>
                {rows}
            </tbody>
        </table>
        """

    # Document delivery configuration
    document_config = {
        "invoice_copy": {
            "name": "Invoice Copy",
            "email": "noreply@odpbusiness.com",
        },
        "credit_memo": {
            "name": "Credit Memo",
            "email": "noreply@odpbusiness.com",
        },
        "soa": {
            "name": "Statement of Account",
            "email": "notification@officedepot.com",
        },
    }

    # Build document delivery section
    document_rows = ""

    for document_type in document_types:
        document_type = str(document_type).strip().lower()

        if document_type in document_config:
            document_name = document_config[document_type]["name"]
            document_email = document_config[document_type]["email"]

            document_rows += f"""
            <li style="margin-bottom:8px;">
                The requested <strong>{document_name}</strong>
                will be sent from
                {document_email}
                    {document_email}
                </a>
                within 24 hours.
            </li>
            """

    document_section = ""

    if document_rows:
        document_section = f"""
        <h3>Document Delivery</h3>

        <p>
            As requested, the following document(s) will be delivered
            separately:
        </p>

        <ul>
            {document_rows}
        </ul>
        """

    # Payment request section based on call status
    if call_status:
        payment_section = """
        <p>
            Please provide the payment details, including check or ACH
            reference, total payment amount, and payment date, if they
            have not already been provided.
        </p>
        """
    else:
        payment_section = """
        <p>
            Please review the outstanding balance and provide the
            payment details, including check or ACH reference, total
            payment amount, and payment date, at your earliest
            convenience.
        </p>
        """

    # Next steps based on call status
    if call_status:
        next_steps_section = """
        <h3>Next Steps</h3>

        <ul>
            <li>Complete the agreed action items.</li>
            <li>Provide payment details when available.</li>
            <li>Schedule another review call if required.</li>
        </ul>
        """
    else:
        next_steps_section = """
        <h3>Next Steps</h3>

        <ul>
            <li>Review the outstanding balance.</li>
            <li>Provide the payment status and payment details.</li>
            <li>Contact us if any invoice requires clarification.</li>
        </ul>
        """

    # Complete HTML email
    email_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >
        <title>Post-Call Email</title>
    </head>

    <body
        style="
            margin:0;
            padding:20px;
            background-color:#ffffff;
            font-family:Arial,Helvetica,sans-serif;
            font-size:14px;
            line-height:1.5;
            color:#000000;
        "
    >
        <div
            style="
                max-width:750px;
                margin:0 auto;
                background-color:#ffffff;
            "
        >
            <p>Dear {customer_name},</p>

            <p>I hope this message finds you well.</p>

            {opening_section}

            {details_section}

            {summary_section}

            {payment_section}

            {action_section}

            {document_section}

            {next_steps_section}

            <p>
                If you have any questions or require further assistance,
                please do not hesitate to contact me directly.
            </p>

            <p>
                Thank you for your prompt attention to this matter.
            </p>

            <p>
                Kind regards,
                <br><br>
                {sender_name}
                <br>
                <a href="mailto:ABBillingSupport@odpbusiness.com">
                    ABBillingSupport@odpbusiness.com
                </a>
            </p>
        </div>
    </body>
    </html>
    """

    return email_body.strip()
################################################3    
def get_post_call_one_email(
    ap_name: str,
    phone_number: str,
    past_due_balance: float,
    document_types=[],  # invoice_copy, soa, credit_memo
    sender_name: str = "",
    call_summary: str = None,
    call_status=True,
) -> str:
    """
    Generate a post call one email in HTML format.

    Args:
        ap_name: Accounts Payable contact name.
        phone_number: Dialed phone number.
        past_due_balance: Outstanding balance.
        call_summary: Summary of the call attempt/discussion.
        sender_name: Signature name.

    Returns:
        HTML formatted email body.
    """
    
    try:
        past_due_balance = float(past_due_balance or 0)
    except (ValueError, TypeError):
        past_due_balance = 0.0

    if call_status:
        opening_section = (
            f"I reached you at {phone_number} to discuss the status of the "
            "outstanding balance on your account."
        )
    else:
        opening_section = (
            f"I attempted to reach you at {phone_number} to discuss the status "
            "of the outstanding balance on your account, but unfortunately, "
            "we were unable to connect."
        )

    document_type_to_display_map = {
        "invoice_copy": "invoice copy",
        "credit_memo": "credit memo",
        "soa": "statement of account",
    }

    requested_docs = [
        document_type_to_display_map[doc]
        for doc in document_types
        if doc in document_type_to_display_map
    ]

    document_section = ""

    if requested_docs:
        if len(requested_docs) == 1:
            docs_text = requested_docs[0]
        elif len(requested_docs) == 2:
            docs_text = " and ".join(requested_docs)
        else:
            docs_text = ", ".join(requested_docs[:-1]) + f", and {requested_docs[-1]}"

        document_section += (
            f"<p>As requested, we will provide {docs_text}, along the day from the emails below:</p>"
        )

        document_section += "<ul>"

        if "invoice_copy" in document_types:
            document_section += (
                "<li>Invoice copies will be received from "
                "<a href='mailto:noreply@odpbusiness.com'>noreply@odpbusiness.com</a></li>"
            )

        if "credit_memo" in document_types:
            document_section += (
                "<li>Credit memos will be received from "
                "<a href='mailto:noreply@odpbusiness.com'>noreply@odpbusiness.com</a></li>"
            )

        if "soa" in document_types:
            document_section += (
                "<li>Statement of account will be received from "
                "<a href='mailto:notification@officedepot.com'>notification@officedepot.com</a></li>"
            )

        document_section += "</ul>"

    email_body = f"""
<html>
<body style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #000000;">
    <p>Dear {ap_name},</p>

    <p>I hope this message finds you well.</p>

    <p>{opening_section}</p>

    <p>
        This is to let you know that there is a past due balance on your account of
        <strong>${past_due_balance:,.2f}</strong>. I kindly ask you to review this and
        provide payment details (Check #/ACH, total amount, and payment date) at your
        earliest convenience.
    </p>

    {document_section}

    <p>
        If you have any questions or require further assistance, please do not hesitate
        to reach out to me directly.
    </p>

    <p>Thank you for your prompt attention to this matter.</p>

    <p>
        Kind regards,
        <br><br>
        {sender_name}
    </p>
</body>
</html>
""".strip()

    return email_body
def get_post_call_summary_email(
    customer_name: str,
    call_date: str,
    outstanding_balance: float,
    aging_details: str = "",
    customer_feedback: str = "",
    dispute_details: str = "",
    additional_details: str = "",
    action_items: list = None,
    document_types: list = None,
    sender_name: str = "",
) -> str:
    """
    Generate a post-call summary email in HTML for successful calls.

    Args:
        customer_name: Customer name.
        call_date: Date of the call.
        outstanding_balance: Outstanding invoice balance.
        aging_details: Age of the invoice(s).
        customer_feedback: Customer feedback regarding payment status.
        dispute_details: Disputes or issues identified.
        additional_details: Risks, concerns, or potential delays.
        action_items: List of dicts with keys 'action', 'owner', 'due_date'.
        sender_name: Email signature name.

    Returns:
        HTML formatted email body.
    """
    
    action_items = action_items or []
    document_types = document_types or []

    document_delivery_section = ""
    if document_types:
        document_delivery_section = "<h3>Document Delivery</h3><ul>"
        if "invoice_copy" in document_types:
            document_delivery_section += (
                "<li>The requested document (Invoice Copy) will be sent by "
                "<a href='mailto:noreply@odpbusiness.com'>noreply@odpbusiness.com</a> within 24 hours.</li>"
            )
        if "credit_memo" in document_types:
            document_delivery_section += (
                "<li>The requested document (Credit Memo) will be sent by "
                "<a href='mailto:noreply@odpbusiness.com'>noreply@odpbusiness.com</a> within 24 hours.</li>"
            )
        if "soa" in document_types:
            document_delivery_section += (
                "<li>The Statement of Account will be sent by "
                "<a href='mailto:notification@officedepot.com'>notification@officedepot.com</a> within 24 hours.</li>"
            )
        document_delivery_section += "</ul>"

    action_rows = ""
    for item in action_items:
        if isinstance(item, dict):
            action_rows += (
                f"<tr><td style='border:1px solid #ddd;padding:8px;'>{item.get('action','')}</td>"
                f"<td style='border:1px solid #ddd;padding:8px;'>{item.get('owner','')}</td>"
                f"<td style='border:1px solid #ddd;padding:8px;'>{item.get('due_date','')}</td></tr>"
            )
        else:
            action_rows += (
                f"<tr><td style='border:1px solid #ddd;padding:8px;' colspan='3'>{item}</td></tr>"
            )

    action_table = ""
    if action_rows:
        action_table = (
            "<h3>Agreed Actions</h3>"
            "<table style='border-collapse:collapse;width:100%;'>"
            "<tr style='background:#f2f2f2;'>"
            "<th style='border:1px solid #ddd;padding:8px;text-align:left;'>Action</th>"
            "<th style='border:1px solid #ddd;padding:8px;text-align:left;'>Owner</th>"
            "<th style='border:1px solid #ddd;padding:8px;text-align:left;'>Due Date</th></tr>"
            f"{action_rows}</table>"
        )

    email_body = f"""
<html>
<body style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #000000;">
    <p>Hi Team,</p>

    <p>Thank you for taking my call today. Below is a summary of the key points discussed:</p>

    <h3>Key Discussion Points</h3>
    <ul>
        <li><strong>Outstanding balance:</strong> ${outstanding_balance:,.2f}</li>
        <li><strong>Aging details:</strong> {aging_details or 'N/A'}</li>
        <li><strong>Customer feedback regarding payment status:</strong> {customer_feedback or 'N/A'}</li>
        <li><strong>Disputes or issues identified:</strong> {dispute_details or 'None'}</li>
        <li><strong>Risks, concerns, or potential delays:</strong> {additional_details or 'None'}</li>
    </ul>

    {action_table}

    {document_delivery_section}

    <h3>Next Steps</h3>
    <ul>
        <li>Follow up on outstanding items.</li>
        <li>Schedule the next review call if required.</li>
    </ul>

    <p>Please let me know if I missed anything or if any updates need to be incorporated.</p>

    <p>
        Kind regards,
        <br><br>
        {sender_name}<br>
        <a href='mailto:ABBillingSupport@odpbusiness.com'>ABBillingSupport@odpbusiness.com</a>
    </p>
</body>
</html>
""".strip()

    return email_body


def get_escalation_email(
    customer_name: str,
    call_summary: str = "",
    escalation_details: dict = None,
    sender_name: str = "",
) -> str:
    """
    Generate an escalation email in HTML format.

    Args:
        customer_name: Customer name.
        call_summary: Summary of the call.
        escalation_details: Dict with escalation_level, escalated_to_role, reason, etc.
        sender_name: Email signature name.

    Returns:
        HTML formatted email body.
    """
    escalation_details = escalation_details or {}

    email_body = f"""
<html>
<body style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #000000;">
    <p>Hi Team,</p>

    <p>An escalation has been raised for customer <strong>{customer_name}</strong>.</p>

    <h3>Escalation Details</h3>
    <ul>
        <li><strong>Escalation Level:</strong> {escalation_details.get('escalation_level', 'L1')}</li>
        <li><strong>Escalated To:</strong> {escalation_details.get('escalated_to_role', 'Senior Representative')}</li>
        <li><strong>Reason:</strong> {escalation_details.get('reason', 'N/A')}</li>
        <li><strong>Target Resolution Date:</strong> {escalation_details.get('target_resolution_date', 'N/A')}</li>
    </ul>

    <h3>Call Summary</h3>
    <p>{call_summary or 'N/A'}</p>

    <p>Please prioritize and follow up at the earliest.</p>

    <p>
        Kind regards,
        <br><br>
        {sender_name}
    </p>
</body>
</html>
""".strip()

    return email_body


def get_post_call_two_email(
    ap_name: str,
    account_number: str,
    past_due_balance: float,
    document_types: list = None,
    sender_name: str = "",
) -> str:
    """
    Generate a post call two email in HTML format.

    Args:
        ap_name: Accounts Payable contact name.
        account_number: Customer account number.
        past_due_balance: Outstanding balance amount.
        document_types: List of document types requested (invoice_copy, soa, credit_memo).
        sender_name: Email signature name.

    Returns:
        HTML formatted email body.
    """
    try:
        past_due_balance = float(past_due_balance or 0)
    except (ValueError, TypeError):
        past_due_balance = 0.0
    document_types = document_types or []

    document_section = ""
    if document_types:
        document_section = (
            "<p>In case you need the invoice copies and the statement of the account "
            "to expedite your payment, we will provide them along the day from the emails below:</p><ul>"
        )
        if "invoice_copy" in document_types:
            document_section += (
                "<li>Invoices will be received from "
                "<a href='mailto:noreply@odpbusiness.com'>noreply@odpbusiness.com</a></li>"
            )
        if "soa" in document_types:
            document_section += (
                "<li>Statement of the account will be received from "
                "<a href='mailto:notification@officedepot.com'>notification@officedepot.com</a></li>"
            )
        document_section += "</ul>"

    email_body = f"""
<html>
<body style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #000000;">
    <p>Dear {ap_name},</p>

    <p>I hope this message finds you well.</p>

    <p>
        I am reaching out regarding the outstanding balance of
        <strong>${past_due_balance:,.2f}</strong> on your account <strong>#{account_number}</strong>.
    </p>

    <p>
        It is imperative that we receive an update on the status of this payment.
        Kindly provide a resolution or an estimated timeframe for when the balance
        will be fully settled.
    </p>

    <p>
        Additionally, once the payment has been processed, please provide the payment
        details, including the check number, check amount, check date, and remittance
        information, so I can update the account records.
    </p>

    {document_section}

    <p>
        Your prompt attention to this matter would be greatly appreciated.
        Please confirm receipt of this email at your earliest convenience.
    </p>

    <p>
        Kind regards,
        <br><br>
        {sender_name}
    </p>
</body>
</html>
""".strip()

    return email_body

class LogicAppEmailHandler:
    def __init__(self):
        self.logic_app_url = os.getenv("LOGIC_APP_URL")

        if not self.logic_app_url:
            raise ValueError(
                "LOGIC_APP_URL environment variable is not configured."
            )

    def send_email(
        self,
        to: list[str],
        subject: str,
        html_body: str,
        cc: list[str] | None = None,
        attachment_path: str | None = None,
    ) -> bool:
        """
        Send email through Azure Logic App.
        """

        payload = {
            "to": to,
            "subject": subject,
            "html_body": html_body,
            "cc": cc or [],
            "attachments": [],
        }

        if attachment_path:
            file_path = Path(attachment_path)

            if not file_path.exists():
                raise FileNotFoundError(
                    f"Attachment not found: {attachment_path}"
                )

            with open(file_path, "rb") as file:
                payload["attachments"].append(
                    {
                        "file_name": file_path.name,
                        "content_base64": base64.b64encode(
                            file.read()
                        ).decode("utf-8"),
                    }
                )

        try:
            response = requests.post(
                self.logic_app_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )

            response.raise_for_status()
            
            print(f"Logic App email request submitted successfully. Status={ response.status_code}",
               )

            logger.info(
                "Logic App email request submitted successfully. Status=%s",
                response.status_code,
            )

            return True

        except requests.exceptions.RequestException as ex:
            print(ex)
            logger.exception(
                "Failed to invoke Logic App email workflow: %s",
                str(ex),
            )
            raise