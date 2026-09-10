import os
import json
import aiohttp
from twilio.rest import Client
import re
from logging import Logger
import requests
from datetime import datetime
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import pytz
from app.agents.postcall_agent.main import process_transcript_and_update_json
from app.config.src import (
    WEBHOOK_URL,
    BLOB_CONTAINER_NAME,
    ORCHESTRATION_LAYER,
    processed_calls,
    account_sid,
    auth_token
)
from app.cosmos.db import save_final_payload_to_cosmos
from app.caller_features.audit_manager import fire_and_forget_log
from app.caller_features.sharepoint import list_sharepoint_folder_contents, upload_file_to_sharepoint
from app.caller_features.email_handler import LogicAppEmailHandler, get_post_call_one_email, get_post_call_two_email, get_post_call_summary_email, get_escalation_email,get_post_call_email
import pandas as pd
import tempfile
import phonenumbers
import pycountry
import pycountry_convert as pc
import logging
import asyncio
logger = logging.getLogger("orchestrator")
EMAIL_SENDER_NAME = os.getenv("EMAIL_SENDER_NAME", "Accounts-receivables")
EMAIL_SENDER_ADDRESS = "Accounts-Receivable@odpbusiness.com"
AB_BILLING_SUPPORT_EMAIL = "ABBillingSupport@odpbusiness.com"
# ESCALATION_CC_LIST = [
#     "Henry.Lopez@theodpgroup.com",
#     "Pamela.Juarez@theodpgroup.com",
#     "Abner.Chex@theodpgroup.com",
# ]
ESCALATION_CC_LIST="merwyn.thomas@theodpgroup.com"
#############################
DELIMITER = "|~"

def get_timezone_by_postal_code(postal_code, country_name):
    """
    Get timezone string from postal code and country.
    """
    geolocator = Nominatim(user_agent="timezone_converter_2026")
    location = geolocator.geocode(f"{postal_code}, {country_name}")

    

    if not location:
        return "Location not found."

    # 2. Find the timezone polygon the coordinates fall into
    tf = TimezoneFinder()
    tz_string = tf.timezone_at(lat=location.latitude, lng=location.longitude)
    
    if not tz_string:
        return "Timezone could not be determined."

    # 3. Get the current time in that localized timezone
    target_timezone = pytz.timezone(tz_string)
    localized_time = datetime.now(target_timezone)
    
    return {
        "Timezone": tz_string,
        "Current Time": localized_time.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
        "result_timestamp": localized_time.strftime("%Y-%m-%d %H:%M:%S")
    }


def get_phone_code_by_postal_code(postal_code, country_name):
    """
    Get phone country code using postal code and country name.

    Returns:
      - ISO alpha-2 country code, example: US
      - ISO alpha-3 country code, example: USA
      - phone calling code, example: +1
    """

    try:
        geolocator = Nominatim(user_agent="phone_code_lookup_application")

        country = pycountry.countries.lookup(country_name)

        query = {
            "postalcode": postal_code,
            "country": country.alpha_2
        }

        location = geolocator.geocode(query, addressdetails=True)

        if not location or "address" not in location.raw:
            return {
                "phone_country": "",
                "phone_code": "",
                "country_alpha_2": "",
                "country_alpha_3": "",
                "error": "Location or country not found for this postal code."
            }

        iso_country_alpha_2 = location.raw["address"].get(
            "country_code",
            ""
        ).upper()

        if not iso_country_alpha_2:
            return {
                "phone_country": "",
                "phone_code": "",
                "country_alpha_2": "",
                "country_alpha_3": "",
                "error": "Could not extract country code from location."
            }

        country_obj = pycountry.countries.get(alpha_2=iso_country_alpha_2)

        if not country_obj:
            return {
                "phone_country": "",
                "phone_code": "",
                "country_alpha_2": iso_country_alpha_2,
                "country_alpha_3": "",
                "error": "Could not map country code."
            }

        calling_code = phonenumbers.country_code_for_region(
            iso_country_alpha_2
        )

        return {
            # "phone_country": country_obj.alpha_3,
            "phone_code": f"+{calling_code}",
            # "country_alpha_2": country_obj.alpha_2,
            # "country_alpha_3": country_obj.alpha_3
        }

    except Exception as e:
        return {
            "phone_country": "",
            "phone_code": "",
            "country_alpha_2": "",
            "country_alpha_3": "",
            "error": str(e)
        }

def get_currency_by_postal_code(postal_code,country_name=None):
    """
    Finds the currency code using a postal code.
  
    """
    # Step 1: Initialize the geolocator with a custom user agent
    geolocator = Nominatim(user_agent="currency_lookup_application")
    
    # Format the query based on available inputs
    query = {"postalcode": postal_code}
    # if country_code:
    #     query["country"] = country_code

    if country_name:
        country = pycountry.countries.lookup(country_name)
        query["country"] = country.alpha_2    
        
    try:
        # Step 2: Geocode the postal code to get country information
        location = geolocator.geocode(query, addressdetails=True)
        
        if not location or 'address' not in location.raw:
            return "Error: Location or country not found for this postal code."
            
        # Extract the 2-letter country code (ISO 3166-1 alpha-2)
        iso_country = location.raw['address'].get('country_code', '').upper()
        
        if not iso_country:
            return "Error: Could not extract country code from location data."
            
        # Step 3: Map the country code to its currency code
        country = pycountry.countries.get(alpha_2=iso_country)
        currency = pycountry.currencies.get(numeric=country.numeric)
        
        return {
            # "postal_code": postal_code,
            # "country_name": country.name,
            # "country_code": iso_country,
            "currency_code": currency.alpha_3,
            # "currency_name": currency.name
        }
        
    except Exception as e:
        return f"An error occurred: {str(e)}"




import logging
from typing import Any, Dict, List, Optional

from app.caller_features.rpa import enqueue_rpa_record


logger = logging.getLogger("orchestrator")


def first_non_empty(*values: Any, default: Any = "") -> Any:
    """
    Return the first value that is not None and not an empty string.
    """

    for value in values:
        if value is not None and value != "":
            return value

    return default


def process_completed_call(
    call_id: str,
    user_data: Dict[str, Any],
    document: Dict[str, Any],
    allowed_actions: List[Dict[str, Any]],
    call_status: Any,
    twilio_metadata: Optional[Dict[str, Any]] = None,
    timezone_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convert post-call results into outbound RPA queue records.

    This function only inserts records into Cosmos DB.
    It does not create files or upload files to SharePoint.
    The scheduled batch process handles file generation and upload.
    """

    twilio_metadata = twilio_metadata or {}
    timezone_info = timezone_info or {}
    allowed_actions = allowed_actions or []
    user_data = user_data or {}
    document = document or {}

    call_id = str(call_id or "")

    customer_number = str(
        first_non_empty(
            user_data.get("customer_number"),
            document.get("customer_number"),
            document.get("customer_id"),
            document.get("user_id"),
            default="",
        )
    )

    organization_id = first_non_empty(
        user_data.get("tenant_id"),
        user_data.get("organization_id"),
        document.get("tenant_id"),
        document.get("organization_id"),
    )

    bill_to = first_non_empty(
        user_data.get("bill_to"),
        user_data.get("bill_to_number"),
        document.get("bill_to"),
        user_data.get("customer_name"),
    )

    customer_name = first_non_empty(
        user_data.get("customer_name"),
        document.get("customer_name"),
    )

    email_address = first_non_empty(
        user_data.get("email_address"),
        user_data.get("email"),
        document.get("email_address"),
        document.get("email"),
    )

    call_data = document.get("call_data") or {}

    queued_records: List[Dict[str, Any]] = []
    failed_records: List[Dict[str, Any]] = []

    def queue_record(
        record_type: str,
        action_id: str,
        payload: Dict[str, Any],
        idempotency_suffix: Optional[str] = None,
    ) -> None:
        """
        Queue one RPA record without stopping the remaining records
        when one insertion fails.
        """

        safe_action_id = str(action_id or "")

        suffix = (
            idempotency_suffix
            or safe_action_id
            or "default"
        )

        idempotency_key = (
            f"{record_type}:{call_id}:{suffix}"
        )

        try:
            created_record = enqueue_rpa_record(
                record_type=record_type,
                call_id=call_id,
                customer_number=customer_number,
                action_id=safe_action_id,
                payload=payload,
                idempotency_key=idempotency_key,
            )

            queued_records.append(
                {
                    "id": created_record.get("id"),
                    "record_type": record_type,
                    "action_id": safe_action_id,
                    "status": created_record.get("status"),
                }
            )

            logger.info(
                "Queued RPA record type=%s call_id=%s action_id=%s",
                record_type,
                call_id,
                safe_action_id,
            )

        except Exception as exc:
            logger.exception(
                "Failed to queue RPA record "
                "type=%s call_id=%s action_id=%s",
                record_type,
                call_id,
                safe_action_id,
            )

            failed_records.append(
                {
                    "record_type": record_type,
                    "action_id": safe_action_id,
                    "error": str(exc),
                }
            )

    # ==============================================================
    # 1. Call result
    # ==============================================================

    if call_status is True:
        normalized_call_status = "completed"
    elif call_status is False:
        normalized_call_status = "failed"
    else:
        normalized_call_status = str(call_status or "")

    twilio_status = first_non_empty(
        twilio_metadata.get("status"),
        document.get("twilio_status"),
        call_data.get("twilio_status"),
        normalized_call_status,
    )

    action_descriptions = [
        {
            "action_id": action.get("action_id"),
            "action_type": action.get("action_type"),
        }
        for action in allowed_actions
        if isinstance(action, dict)
    ]

    queue_record(
        record_type="call_result",
        action_id="CALL_RESULT",
        payload={
            "org_id": organization_id,
            "customer_number": customer_number,
            "bill_to": bill_to,
            "follow_up_date": first_non_empty(
                document.get("follow_up_date"),
                call_data.get("follow_up_date"),
            ),
            "next_action": action_descriptions,
            "result_description": first_non_empty(
                document.get("summary"),
                normalized_call_status,
            ),
            "result_code": twilio_status,
            "result_timestamp": first_non_empty(
                timezone_info.get("result_timestamp"),
                call_data.get("call_end_at"),
                twilio_metadata.get("end_time"),
            ),
        },
        idempotency_suffix="call-result",
    )

    # ==============================================================
    # 2. Contact update
    # ==============================================================

    contact_payload = {
        "org_id": organization_id,
        "customer_number": customer_number,
        "bill_to": bill_to,
        "contact_name_1": first_non_empty(
            user_data.get("contact_name_1"),
            customer_name,
        ),
        "contact_name_2": user_data.get(
            "contact_name_2",
            "",
        ),
        "email": email_address,
        "phone_country": user_data.get(
            "phone_country",
            "",
        ),
        "phone": user_data.get(
            "phone",
            "",
        ),
        "cell_country": user_data.get(
            "cell_country",
            "",
        ),
        "cell_phone": user_data.get(
            "cell_phone",
            "",
        ),
        "other_phone_country": user_data.get(
            "other_phone_country",
            "",
        ),
        "other_phone": user_data.get(
            "other_phone",
            "",
        ),
        "fax_country": user_data.get(
            "fax_country",
            "",
        ),
        "fax": user_data.get(
            "fax",
            "",
        ),
        "preferred_language": user_data.get(
            "preferred_language",
            "",
        ),
        "contact_sequence": user_data.get(
            "contact_sequence",
            "1",
        ),
    }

    has_contact_data = any(
        [
            contact_payload["email"],
            contact_payload["phone"],
            contact_payload["cell_phone"],
            contact_payload["other_phone"],
            contact_payload["fax"],
        ]
    )

    if has_contact_data:
        queue_record(
            record_type="contact",
            action_id="CONTACT_UPDATE",
            payload=contact_payload,
            idempotency_suffix="contact-update",
        )

    # ==============================================================
    # 3. Special notes
    # ==============================================================

    special_notes = first_non_empty(
        document.get("special_notes"),
        document.get("notes"),
    )

    if special_notes:
        queue_record(
            record_type="special_notes",
            action_id="SPECIAL_NOTES",
            payload={
                "Organization ID": organization_id,
                "Customer #": customer_number,
                "Bill To #": bill_to,
                "Note text": first_non_empty(
                    document.get("summary"),
                    special_notes,
                ),
                "Note Date": first_non_empty(
                    timezone_info.get("Current Time"),
                    call_data.get("call_end_at"),
                    twilio_metadata.get("end_time"),
                ),
                "Note Entered By": first_non_empty(
                    document.get("note_entered_by"),
                    user_data.get("collector_name"),
                    "Lisa",
                ),
                "Special Instruction Note": special_notes,
                "Special Notes": special_notes,
            },
            idempotency_suffix="special-notes",
        )

    # ==============================================================
    # 4. Action-specific records
    # ==============================================================

    for action_index, action in enumerate(
        allowed_actions,
        start=1,
    ):
        if not isinstance(action, dict):
            logger.warning(
                "Skipping invalid action call_id=%s action=%r",
                call_id,
                action,
            )
            continue

        action_type = str(
            action.get("action_type") or ""
        ).strip()

        action_id = str(
            action.get("action_id")
            or f"ACTION_{action_index}"
        )

        # ----------------------------------------------------------
        # Promise to pay
        # ----------------------------------------------------------

        if action_type == "PromiseToPay":
            details = (
                action.get("promise_to_pay_details")
                or {}
            )

            if not isinstance(details, dict):
                details = {}

            queue_record(
                record_type="promise_to_pay",
                action_id=action_id,
                payload={
                    "org_id": organization_id,
                    "customer_number": customer_number,
                    "bill_to": bill_to,
                    "invoice_id": first_non_empty(
                        details.get("invoice_id"),
                        details.get("invoice_number"),
                        action.get("invoice_id"),
                        action.get("document_number"),
                    ),
                    "promise_to_pay_date": first_non_empty(
                        details.get("promise_to_pay_date"),
                        details.get("payment_date"),
                        action.get("promise_to_pay_date"),
                    ),
                    "promise_to_pay_amount": first_non_empty(
                        details.get("promise_to_pay_amount"),
                        details.get("amount"),
                        action.get("promise_to_pay_amount"),
                    ),
                    "currency": first_non_empty(
                        details.get("currency"),
                        action.get("currency"),
                        user_data.get("currency"),
                    ),
                },
            )

        # ----------------------------------------------------------
        # NPR / Doubtful payment
        # ----------------------------------------------------------

        elif action_type == "DoubtfulPayment":
            queue_record(
                record_type="npr",
                action_id=action_id,
                payload={
                    "org_id": organization_id,
                    "customer_number": customer_number,
                    "bill_to": bill_to,
                    "invoice_id": first_non_empty(
                        action.get("invoice_id"),
                        action.get("document_number"),
                        action.get("invoice_number"),
                    ),
                    "npr_code": first_non_empty(
                        action.get("npr_code"),
                        action.get("reason_code"),
                    ),
                },
            )

        # ----------------------------------------------------------
        # Dispute
        # ----------------------------------------------------------

        elif action_type == "Dispute":
            dispute_details = (
                action.get("dispute_details")
                or {}
            )

            if not isinstance(dispute_details, dict):
                dispute_details = {}

            queue_record(
                record_type="dispute",
                action_id=action_id,
                payload={
                    "org_id": organization_id,
                    "customer_number": customer_number,
                    "bill_to": bill_to,
                    "invoice_id": first_non_empty(
                        dispute_details.get("invoice_id"),
                        dispute_details.get("invoice_number"),
                        action.get("invoice_id"),
                        action.get("document_number"),
                    ),
                    "dispute": first_non_empty(
                        dispute_details.get("dispute"),
                        dispute_details.get("reason"),
                        action.get("dispute"),
                    ),
                    "dispute_type": first_non_empty(
                        dispute_details.get("dispute_type"),
                        action.get("dispute_type"),
                    ),
                    "dispute_date": first_non_empty(
                        dispute_details.get("dispute_date"),
                        timezone_info.get("Current Time"),
                    ),
                    "dispute_amount": first_non_empty(
                        dispute_details.get("dispute_amount"),
                        dispute_details.get("amount"),
                    ),
                    "currency": first_non_empty(
                        dispute_details.get("currency"),
                        action.get("currency"),
                        user_data.get("currency"),
                    ),
                    "dispute_status": first_non_empty(
                        dispute_details.get("dispute_status"),
                        action.get("dispute_status"),
                    ),
                    "dispute_status_date": first_non_empty(
                        dispute_details.get(
                            "dispute_status_date"
                        ),
                        action.get("dispute_status_date"),
                    ),
                },
            )

        # ----------------------------------------------------------
        # SOA
        # ----------------------------------------------------------

        elif action_type == "InvoiceCopyCreditMemo":
            queue_record(
                record_type="soa",
                action_id=action_id,
                payload={
                    "org_id": organization_id,
                    "customer_number": customer_number,
                    "bill_to": bill_to,
                    "email_address": email_address,
                },
            )

        # ----------------------------------------------------------
        # Document copy
        # ----------------------------------------------------------

        elif action_type == "DocumentCopy":
            invoice_details = (
                action.get("invoice_details")
                or document.get("invoice_details")
                or []
            )

            if not isinstance(invoice_details, list):
                invoice_details = [invoice_details]

            if not invoice_details:
                queue_record(
                    record_type="document_copy",
                    action_id=action_id,
                    payload={
                        "Bill To # - Account": bill_to,
                        "Document #": "",
                        "Transaction Balance Due": "",
                        "Contact Email Address": email_address,
                        "Customer #": customer_number,
                    },
                    idempotency_suffix=(
                        f"{action_id}:no-invoice"
                    ),
                )

            for invoice_index, invoice in enumerate(
                invoice_details,
                start=1,
            ):
                if isinstance(invoice, dict):
                    invoice_data = invoice
                else:
                    invoice_data = {
                        "invoice_number": invoice,
                    }

                invoice_number = first_non_empty(
                    invoice_data.get("invoice_number"),
                    invoice_data.get("invoice_no"),
                    invoice_data.get("invoice_id"),
                    invoice_data.get("document_number"),
                )

                queue_record(
                    record_type="document_copy",
                    action_id=action_id,
                    payload={
                        "Bill To # - Account": bill_to,
                        "Document #": invoice_number,
                        "Transaction Balance Due": first_non_empty(
                            invoice_data.get(
                                "outstanding_balance"
                            ),
                            invoice_data.get("due_balance"),
                            invoice_data.get("balance_due"),
                        ),
                        "Contact Email Address": email_address,
                        "Customer #": customer_number,
                    },
                    idempotency_suffix=(
                        f"{action_id}:"
                        f"{invoice_number or invoice_index}"
                    ),
                )

        # ----------------------------------------------------------
        # Unsupported action
        # ----------------------------------------------------------

        else:
            logger.warning(
                "Unsupported post-call action type=%s "
                "call_id=%s action_id=%s",
                action_type,
                call_id,
                action_id,
            )

    # ==============================================================
    # 5. Build processing result
    # ==============================================================

    if failed_records:
        if queued_records:
            overall_status = "partially_queued"
        else:
            overall_status = "failed"
    else:
        overall_status = "queued"

    return {
        "status": overall_status,
        "call_id": call_id,
        "customer_number": customer_number,
        "queued_count": len(queued_records),
        "failed_count": len(failed_records),
        "queued_records": queued_records,
        "failed_records": failed_records,
        "flags": {
            "rpa_triggered": any(
                record["record_type"] == "document_copy"
                for record in queued_records
            ),
            "notes_triggered": any(
                record["record_type"] == "special_notes"
                for record in queued_records
            ),
            "npr_triggered": any(
                record["record_type"] == "npr"
                for record in queued_records
            ),
            "dispute_triggered": any(
                record["record_type"] == "dispute"
                for record in queued_records
            ),
            "promise_to_pay_triggered": any(
                record["record_type"] == "promise_to_pay"
                for record in queued_records
            ),
            "call_result_triggered": any(
                record["record_type"] == "call_result"
                for record in queued_records
            ),
            "contacts_details_triggered": any(
                record["record_type"] == "contact"
                for record in queued_records
            ),
            "soa_triggered": any(
                record["record_type"] == "soa"
                for record in queued_records
            ),
        },
    }


def get_contact_records(
    org_id,
    customer_number,
    bill_to,
    contact_name_1,
    contact_name_2,
    email,
    phone_country,
    phone,
    cell_country,
    cell_phone,
    other_phone_country,
    other_phone,
    fax_country,
    fax,
    preferred_language,
    contact_sequence,
    batch_number="001"
):

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_date = datetime.now().strftime("%Y%m%d")

    # Header Record
    header_record = (
        f"*HEADER{DELIMITER}"
        f"CGOD_WC{DELIMITER}"
        f"{timestamp}"
    )

    # Contact Record
    contact_record = DELIMITER.join([
        str(org_id or ""),
        str(customer_number or ""),
        str(bill_to or ""),
        str(contact_name_1 or ""),
        str(contact_name_2 or ""),
        str(email or ""),
        str(customer_phone_code.get("phone_code", "")),
        str(phone or ""),
        str(customer_phone_code.get("phone_code", "")),
        str(cell_phone or ""),
        str(other_phone_country or ""),
        str(other_phone or ""),
        str(fax_country or ""),
        str(fax or ""),
        str(preferred_language or ""),
        str(contact_sequence or "")
    ])

    # Trailer Record
    trailer_record = "*TRAILER"

    file_content = "\n".join([
        header_record,
        contact_record,
        trailer_record
    ])

    filename = (
        f"contact_update_"
        f"{file_date}_"
        f"{batch_number}.csv"
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".csv"
    ) as temp_file:

        temp_file.write(file_content)
        temp_path = temp_file.name

    final_path = os.path.join(
        os.path.dirname(temp_path),
        filename
    )

    os.rename(temp_path, final_path)

    print(
        f"[WebCollect] Prepared contact record "
        f"for customer: {customer_number}"
    )

    return final_path
#################################promise_to_pay########################################
     






def get_promise_to_pay_records(
    org_id,
    customer_number,
    bill_to,
    invoice_id,
    promise_to_pay_date,
    promise_to_pay_amount,
    currency=None,
    batch_number="001"
):

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_date = datetime.now().strftime("%Y%m%d")

    # Header
    header_record = (
        f"*HEADER{DELIMITER}"
        f"CGOD_WC{DELIMITER}"
        f"{timestamp}"
    )

    # Data Record
    data_record = DELIMITER.join([
        str(org_id or ""),
        str(customer_number or ""),
        str(bill_to or ""),
        str(invoice_id or ""),
        str(promise_to_pay_date or ""),
        str(promise_to_pay_amount or ""),
        str(currency or dispute_currency.get("currency_code", ""))
    ])

    # Trailer
    trailer_record = "*TRAILER"

    file_content = "\n".join([
        header_record,
        data_record,
        trailer_record
    ])

    filename = (
        f"promise_to_pay_{file_date}_{batch_number}.csv"
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".csv"
    ) as temp_file:
        temp_file.write(file_content)
        temp_path = temp_file.name

    final_path = os.path.join(
        os.path.dirname(temp_path),
        filename
    )

    os.rename(temp_path, final_path)

    print(f"[WebCollect] Generated file: {final_path}")

    return final_path
#########################special######################################################
def get_special_notes(special_notes,org_id,customer_number,bill_to,note,note_entered_by=None,special_instruction=None):  
    def get_note_record(special_notes,org_id,customer_number,bill_to,note,note_entered_by=None,special_instruction=None):

       note_record = {
        "Organization ID": org_id,
        "Customer #": customer_number,
        "Bill To #": bill_to,
        "Note text": note,
        "Note Date": timezone_str.get("Current Time", " "),
        "Note Entered By": note_entered_by,
        "Special Instruction Note": special_instruction,
        "Special Notes": special_notes
       }

       return note_record

    note_records = [get_note_record(special_notes=special_notes,org_id=org_id,customer_number=customer_number,bill_to=bill_to,note=note,note_entered_by=note_entered_by,special_instruction=special_instruction)]

    # note_records = [get_note_record(org_id,customer_number,bill_to,note,note_entered_by,special_instruction)]
    print(f"[RPA] Prepared single record for customer: {customer_number}")
    df =  pd.DataFrame(note_records)
    print(df)
    excel_path1 = None
    local=False
    if(not local):
        with tempfile.NamedTemporaryFile(
            suffix=".xlsx",
            delete=False
        ) as temp_file:

            excel_path1 = temp_file.name
        
    else:
        excel_path = f"{customer_number}.xlsx"
    df.to_excel(excel_path1, index=False)
    return excel_path1


def send_notes_rpa_sharepoint(file_path):
    file_name1=os.path.basename(file_path)
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/outbound"
    uploaded=upload_file_to_sharepoint(file_path, sp_folder_path, file_name=f"{file_name1}.xlsx")
    print(f"[SharePoint] RPA Excel uploaded for {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False


################################################npr##############
def send_npr(org_id, customer_number, bill_to, invoice_id, npr_code, batch_number="001"):

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_date = datetime.now().strftime("%Y%m%d")

    # Header Record
    header_record = (
        f"*HEADER{DELIMITER}"
        f"CGOD_WC{DELIMITER}"
        f"{timestamp}"
    )

    # Data Record
    data_record = DELIMITER.join([
        str(org_id or ""),
        str(customer_number or ""),
        str(bill_to or ""),
        str(invoice_id or ""),
        str(npr_code or "")
    ])

    # Trailer Record
    trailer_record = "*TRAILER"

    file_content = "\n".join([
        header_record,
        data_record,
        trailer_record
    ])

    filename = (
        f"npr_{file_date}_{batch_number}.csv"
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".csv"
    ) as temp_file:
        temp_file.write(file_content)
        temp_path = temp_file.name

    final_path = os.path.join(
        os.path.dirname(temp_path),
        filename
    )

    os.rename(temp_path, final_path)

    print(f"[WebCollect] Generated NPR file: {final_path}")

    return final_path
        
def send_to_nrp_sharepoint(file_path):
    file_name1=os.path.basename(file_path)
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/outbound"
    uploaded=upload_file_to_sharepoint(file_path, sp_folder_path, file_name=file_name1)
    print(f"[SharePoint] RPA NPR file uploaded: {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False


def send_promise_to_pay_rpa_sharepoint(file_path):
    file_name1=os.path.basename(file_path)
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/outbound"
    uploaded=upload_file_to_sharepoint(file_path, sp_folder_path, file_name=file_name1)
    print(f"[SharePoint] RPA Promise to Pay file uploaded: {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False


def send_call_result_rpa_sharepoint(file_path):
    file_name1=os.path.basename(file_path)
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/outbound"
    uploaded=upload_file_to_sharepoint(file_path, sp_folder_path, file_name=file_name1)
    print(f"[SharePoint] RPA Call Result file uploaded: {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False


def send_contact_rpa_sharepoint(file_path):
    file_name1=os.path.basename(file_path)
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/outbound"
    uploaded=upload_file_to_sharepoint(file_path, sp_folder_path, file_name=file_name1)
    print(f"[SharePoint] RPA Contact file uploaded: {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False


################################dispute##################################################
def send_dispute_records(
    org_id,
    customer_number,
    bill_to,
    invoice_id,
    dispute,
    dispute_type,
    dispute_date,
    dispute_amount,
    currency,
    dispute_status,
    dispute_status_date,
    batch_number="001"
):

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_date = datetime.now().strftime("%Y%m%d")

    # Header Record
    header_record = (
        f"*HEADER{DELIMITER}"
        f"CGOD_WC{DELIMITER}"
        f"{timestamp}"
    )

    # Data Record
    data_record = DELIMITER.join([
        str(org_id or ""),
        str(customer_number or ""),
        str(bill_to or ""),
        str(invoice_id or ""),
        str(dispute or ""),
        str(dispute_type or ""),
        str(dispute_date or timezone_str.get("Current Time", "")),
        str(dispute_amount or ""),
        str(currency or dispute_currency.get("currency_code", "")),
        str(dispute_status or ""),
        str(dispute_status_date or "")
    ])

    # Trailer Record
    trailer_record = "*TRAILER"

    file_content = "\n".join([
        header_record,
        data_record,
        trailer_record
    ])

    filename = (
        f"dispute_{file_date}_{batch_number}.csv"
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".csv"
    ) as temp_file:
        temp_file.write(file_content)
        temp_path = temp_file.name

    final_path = os.path.join(
        os.path.dirname(temp_path),
        filename
    )

    os.rename(temp_path, final_path)

    print(f"[WebCollect] Generated dispute file: {final_path}")

    return final_path

def send_dispute_rpa_sharepoint(file_path, customer_number=None):
    file_name1 = os.path.basename(file_path)
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/outbound"
    uploaded = upload_file_to_sharepoint(file_path, sp_folder_path, file_name=file_name1)
    print(f"[SharePoint] RPA Dispute file uploaded: {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False
################################ invoice_rpa ###################
def get_rpa_excel_df(invoice_details, customer_email, bill_to = None, customer_number = None, multiple_invoices = True, local = False, organization_id = None):
    
    def get_record(invoice_id, customer_email, bill_to = None, due_balance = None, customer_number = None, multiple_invoices = False):
        print(f"[RPA] Preparing record for invoice: {invoice_id}, customer: {customer_email}")
        if(multiple_invoices):
            
            record = {
                "Bill To # - Account": bill_to,
                "Document #": invoice_id,
                "Transaction Balance Due": due_balance,
                "Contact Email Address": customer_email,
                "Customer #": customer_number
                  
            }
        else:
            record = {
                "Invoice": invoice_id,
                "Email": customer_email,
                "Status": None 
            }
            
    
        return record
        
    if(not multiple_invoices):
        assert len(invoice_details) > 0
        records = [get_record(invoice_details[0]["invoice_number"], customer_email, multiple_invoices = multiple_invoices)]
        print(f"[RPA] Prepared single record for customer: {customer_email}")
        df =  pd.DataFrame(records)
    else:
        records = [get_record(detail["invoice_number"], customer_email, bill_to = bill_to, due_balance=detail["outstanding_balance"], customer_number=customer_number, multiple_invoices = multiple_invoices) for detail in  invoice_details]
        print(f"[RPA] Prepared {len(records)} records for customer: {customer_email}")
        df =  pd.DataFrame(records)
   
    excel_path = None
    if(not local):
        with tempfile.NamedTemporaryFile(
            suffix=".xlsx",
            delete=False
        ) as temp_file:

            excel_path = temp_file.name
        
    else:
        excel_path = f"{customer_email}.xlsx"
    df.to_excel(excel_path, index=False)
    return excel_path
    # return df

##action
##############################################################################3


def get_call_results(
    org_id,
    customer_number,
    bill_to,
    follow_up_date,
    next_action,
    result_description,
    result_code,
    result_timestamp,
    batch_number="001"
):

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_date = datetime.now().strftime("%Y%m%d")

    # Header
    header_record = (
        f"*HEADER{DELIMITER}"
        f"CGOD_WC{DELIMITER}"
        f"{timestamp}"
    )

    # Data Record
    data_record = DELIMITER.join([
        str(org_id or ""),
        str(customer_number or ""),
        str(bill_to or ""),
        str(follow_up_date or ""),
        str(next_action or ""),
        str(result_description or ""),
        str(result_code or ""),
        str(
            timezone_str.get(
                "result_timestamp",
                result_timestamp or ""
            )
        )
    ])

    # Trailer
    trailer_record = "*TRAILER"

    file_content = "\n".join([
        header_record,
        data_record,
        trailer_record
    ])

    filename = (
        f"call_result_"
        f"{file_date}_"
        f"{batch_number}.csv"
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".csv"
    ) as temp_file:

        temp_file.write(file_content)
        file_path = temp_file.name

    # Rename to desired naming convention
    final_path = os.path.join(
        os.path.dirname(file_path),
        filename
    )

    os.rename(file_path, final_path)

    print(f"[WebCollect] Generated file : {final_path}")

    return final_path
#########################################################################
def get_soa_records( org_id,customer_number,bill_to,email_address,batch_number="001"):

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_date = datetime.now().strftime("%Y%m%d")    
        header_record = (
            f"*HEAD {DELIMITER}"
            f"CGOD_WC{DELIMITER}"
            f"{timestamp}"
        )
    
        # Data Record
        data_record = DELIMITER.join([
            str(org_id or ""),
            str(customer_number or ""),
            str(bill_to or ""),
            str(email_address or ""),
            
        ])
    
        # Trailer
        trailer_record = "*TRAILER"
    
        file_content = "\n".join([
            header_record,
            data_record,
            trailer_record
        ])
    
        filename = (
            f"call_result_"
            f"{file_date}_"
            f"{batch_number}.csv"
        )
    
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            suffix=".csv"
        ) as temp_file:
    
            temp_file.write(file_content)
            file_path = temp_file.name
    
        # Rename to desired naming convention
        final_path = os.path.join(
            os.path.dirname(file_path),
            filename
        )
    
        os.rename(file_path, final_path)
    
        print(f"[WebCollect] Generated file : {final_path}")
    
        return final_path
   

def send_soa_rpa_sharepoint(soa_file_path):
    file_name1=os.path.basename(soa_file_path)
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/outbound"
    uploaded=upload_file_to_sharepoint(soa_file_path, sp_folder_path, file_name=file_name1)
    print(f"[SharePoint] RPA SOA file uploaded: {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False
####################################################################3
def split_actions_by_overall_confidence(
    actions: list[dict],
    confidence_score: float,
    confidence_reason: str,
    min_confidence: float = 0.85,
) -> tuple[list[dict], list[dict]]:
    if confidence_score is None or confidence_score < min_confidence:
        return [], [
            {
                **action,
                "validation_status": "blocked",
                "validation_reason": "Blocked because overall confidence score is below threshold."
            }
            for action in actions
        ]
 
    if not confidence_reason:
        return [], [
            {
                **action,
                "validation_status": "blocked",
                "validation_reason": "Blocked because overall confidence reason is missing."
            }
            for action in actions
        ]
 
    matches = re.findall(
        r"action item ids?\s*(?:is|are|:)?\s*([\d,\s]+)",
        confidence_reason.lower()
    )
 
    action_numbers = set()
 
    for match in matches:
        for num in re.findall(r"\d+", match):
            action_numbers.add(int(num))
 
    allowed_action_ids = {
        f"ACT{num:03d}" for num in action_numbers
    }
 
    allowed_actions = []
    blocked_actions = []
 
    for action in actions:
        action_id = action.get("action_id")
 
        if action_id in allowed_action_ids:
            allowed_actions.append({
                **action,
                "validation_status": "allowed",
                "validation_reason": "Allowed because action ID was referenced in overall confidence reason."
            })
        else:
            blocked_actions.append({
                **action,
                "validation_status": "blocked",
                "validation_reason": "Blocked because action ID was not referenced in overall confidence reason."
            })
 
    return allowed_actions, blocked_actions

postal_code ="10001"
country_name="United States"
country_code="US"
dispute_currency = get_currency_by_postal_code(postal_code, country_name)

customer_phone_code=get_phone_code_by_postal_code(postal_code, country_name)

timezone_str = get_timezone_by_postal_code(
    postal_code,
    country_name
)

def notify_agent_orchestration_service(call_id):
    """
    Notify orchestration service that this agent has completed its current call.

    Required env variables:
      AGENT_ID
      ORCHESTRATION_SERVICE_URL
      CURRENT_CALL_ID

    Optional env variables:
      ORCHESTRATION_NOTIFY_TIMEOUT_SECONDS
    """

    agent_id = os.getenv("AGENT_ID")
    orchestration_service_url = os.getenv("ORCHESTRATION_SERVICE_URL")

    timeout_seconds = int(
        os.getenv("ORCHESTRATION_NOTIFY_TIMEOUT_SECONDS", "10")
    )

    if not agent_id:
        raise ValueError("AGENT_ID env variable is required")

    if not orchestration_service_url:
        raise ValueError("ORCHESTRATION_SERVICE_URL env variable is required")

    if not call_id:
        raise ValueError("CURRENT_CALL_ID env variable is required")

    orchestration_service_url = orchestration_service_url.rstrip("/")

    callback_url = f"{orchestration_service_url}/call_completion"

    payload = {
        "agent_id": agent_id,
        "call_id": call_id,
        "status": "completed",
        "result": {
            "message": "Call completed successfully"
        },
        "error": None,
    }

    try:
        response = requests.post(
            callback_url,
            json=payload,
            timeout=timeout_seconds,
        )

        response.raise_for_status()

        logger.info(
            "Successfully notified orchestration service for call_id=%s, agent_id=%s",
            call_id,
            agent_id,
        )

        return response.json()

    except requests.exceptions.ReadTimeout:
        logger.warning(
            "Timeout notifying orchestration service for call_id=%s",
            call_id
        )

        return {
            "status": "timeout"
        }

    except requests.exceptions.RequestException as exc:
        logger.exception(
            "Failed to notify orchestration service"
        )

        return {
            "status": "failed",
            # "error": str(exc)
        }



def send_to_rpa_sharepoint(invoice_details, customer_email, customer_number = None, bill_to = None, multiple_invoices = True, local = False, organization_id = None):
    rpa_excel_path = get_rpa_excel_df(
        invoice_details,
        customer_email,
        bill_to=bill_to,
        customer_number=customer_number,
        multiple_invoices=multiple_invoices,
        local=local,
        organization_id=organization_id,
    )
    sp_folder_path = f"Accounts Receivable Collections/Agentic for Collections/dev/{'Input1byN/' if multiple_invoices else 'Input1by1/'}"
    uploaded=upload_file_to_sharepoint(rpa_excel_path, sp_folder_path, file_name=f"{customer_email}.xlsx")
    print(f"[SharePoint] RPA Excel uploaded for {customer_email}: {uploaded}")
    if len(uploaded) > 1:
       return True
    else:
        return False


async def report_call_result(call_sid: str, user_data: dict, transcript: list, call_status: bool, recording_info: dict = None, failure_message: str = None, realtime_usage: dict = None,telemetry_summary=None,):
    """
    Collates call metadata, fetches Twilio details, and triggers post-call analysis.
    """
    if call_sid in processed_calls:
        return
    processed_calls.add(call_sid)
   
    try:
        # 1. Fetch Twilio Metadata
        twilio_metadata = {}
        try:
            if account_sid and auth_token:
                twilio_client = Client(account_sid, auth_token)
                call_details = twilio_client.calls(call_sid).fetch()
                twilio_metadata['start_time'] = str(call_details.start_time) if call_details.start_time else ""
                twilio_metadata['end_time'] = str(call_details.end_time) if call_details.end_time else ""
               
        except Exception:
            pass
 
        # 2. Prepare Document for Analysis
        document = user_data.copy()
        if "call_data" not in document or not isinstance(document["call_data"], dict):
            document["call_data"] = {}
       
        # Merge Timestamps
        document["call_data"]["call_start_at"] = twilio_metadata.get('start_time') or document["call_data"].get("call_start_at") or ""
        document["call_data"]["call_end_at"] = twilio_metadata.get('end_time') or document["call_data"].get("call_end_at") or ""
       
        document.update({
            "id": user_data.get("case_id", call_sid),
            "user_id": user_data.get("customer_number") or user_data.get("customer_id", call_sid),
            "customer_name": user_data.get("customer_name"," "),
            "call_sid": call_sid,
            "transcript": transcript
        })
       
        # 3. AI Post-Call Analysis (Extract actions, summary, etc.)
        if transcript:
            try:
                document = await process_transcript_and_update_json(document)
                fire_and_forget_log(user_data, "Call Summary and Transcript Generation", "callhandler_e005", True)
            except Exception as e:
                print(f"[Analysis Error] {e}")
                fire_and_forget_log(user_data, "Call Summary and Transcript Generation", "callhandler_e005", False, "Call summary, transcripts, or notes generation failed due to processing error")
                document['summary'] = "Processing error"
                document['actions'] = []
 
        else:
            document['summary'] = "No conversation recorded."
            document['actions'] = []
            print(f"[Actions] No actions extracted.{document['actions']}")
 
        
        allowed_actions = [action for action in document.get("actions") if action.get("confidence_score", 0) > 0.85] or []
        blocked_actions  = [action for action in document.get("actions") if action.get("confidence_score", 0) <= 0.85] or []
            
        # 4. Construct Final Structured Payload
        full_transcript_obj = {str(i+1): msg for i, msg in enumerate(document.get("transcript") or [])}
       
        # Robust Invoice Extraction
        invoice_nos = []
        # Check invoice_details list
        details = document.get("invoice_details") or []
        if isinstance(details, list):
            for item in details:
                if isinstance(item, dict):
                    # Check common keys: invoice_number, invoice_no, inv_no, etc.
                    raw_inv = (item.get("invoice_number") or item.get("invoice_no") or
                               item.get("inv_no") or item.get("invoiceNumber"))
                    if isinstance(raw_inv, list):
                        invoice_nos.extend([str(x) for x in raw_inv])
                    elif raw_inv:
                        invoice_nos.append(str(raw_inv))
                elif isinstance(item, (str, int)):
                    invoice_nos.append(str(item))
       
        # Fallback to top-level fields if list is empty
        if not invoice_nos:
            top_inv = document.get("invoice_numbers") or document.get("invoice_number") or document.get("invoice_no")
            if isinstance(top_inv, list):
                invoice_nos.extend([str(x) for x in top_inv])
            elif top_inv:
                invoice_nos.append(str(top_inv))
       
        # Unique and cleaned (preserving order)
        invoice_nos = list(dict.fromkeys([x.strip() for x in invoice_nos if x]))
       
       #save_csv_in_sharepoint
        
        is_notes_triggered=False
        spcl=document.get("special_notes") or document.get("notes") or " "
        if spcl is not None:
            notes_triggered=get_special_notes(special_notes=spcl,org_id=user_data.get("tenant_id", None),bill_to=user_data.get("customer_name"),note=user_data.get("note"," "),note_entered_by="Lisa",special_instruction=spcl,customer_number=user_data.get("customer_number"," "))
            notes_sent=send_notes_rpa_sharepoint(notes_triggered)
            if notes_sent==True:
               is_notes_triggered= True
       
        is_rpa_triggered = False
        for action in allowed_actions:
            if(action['action_type'] == "DocumentCopy"):
                # triggered=send_to_rpa_sharepoint(invoice_nos, user_data.get("email_address", "abc@gmail.com"), organization_id=user_data.get("tenant_id", None))
                triggered=send_to_rpa_sharepoint(invoice_details=document.get("invoice_details", []), customer_email=user_data.get("email_address", "abc@gmail.com"), organization_id=user_data.get("tenant_id", None)) 
                if triggered==True:
                    is_rpa_triggered = True
                    
                 # is_rpa_triggered = True
                print(is_rpa_triggered)
        is_npr_triggered=False
        for action in allowed_actions:
            if(action['action_type'] == "DoubtfulPayment"):
                npr_code=action.get("npr_code"," ")
                triggered=send_npr(org_id=user_data.get("tenant_id", None),customer_number=user_data.get("customer_number"),bill_to=user_data.get("customer_name"),invoice_id=action.get("document_number"," "),npr_code=npr_code)
                npr_sent=send_to_nrp_sharepoint(triggered)
                if npr_sent==True:
                    is_npr_triggered = True  
        is_dispute_triggered=False
        for action in allowed_actions:
            if(action['action_type'] == "Dispute"):
                dispute_details=action.get("dispute_details",{})
                dispute_file=send_dispute_records(org_id=user_data.get("tenant_id", None),customer_number=user_data.get("customer_number"),bill_to=user_data.get("customer_name"),invoice_id=dispute_details.get("invoice_id"," "),dispute=dispute_details.get("dispute"," "),dispute_type=dispute_details.get("dispute_type"," "),dispute_date=None,dispute_amount=dispute_details.get("dispute_amount"," "),currency=None,dispute_status=dispute_details.get("dispute_status"," "),dispute_status_date=None)
                dispute_sent=send_dispute_rpa_sharepoint(dispute_file, customer_number=user_data.get("customer_number"))
                if dispute_sent==True:
                    is_dispute_triggered = True

        is_promise_to_pay_triggered=False
        for action in allowed_actions:
            if(action['action_type'] == "PromiseToPay"):
                promise_to_pay_details=action.get("promise_to_pay_details",{})
                triggered=get_promise_to_pay_records(org_id=user_data.get("tenant_id", None),customer_number=user_data.get("customer_number"),bill_to=user_data.get("customer_name"),invoice_id=promise_to_pay_details.get("invoice_id"," "),promise_to_pay_date=promise_to_pay_details.get("promise_to_pay_date"," "),promise_to_pay_amount=promise_to_pay_details.get("promise_to_pay_amount"," "),currency=None)
                promise_sent=send_promise_to_pay_rpa_sharepoint(triggered)
                if promise_sent==True:
                    is_promise_to_pay_triggered = True    


        is_soa_triggered=False
        for action in allowed_actions:
            if(action['action_type'] == "InvoiceCopyCreditMemo"):
           
                soa_triggered=get_soa_records(org_id=user_data.get("tenant_id", None),customer_number=user_data.get("customer_number"),bill_to=user_data.get("customer_name"),email_address=user_data.get("email_address", " "))
                soa_sent=send_soa_rpa_sharepoint(soa_triggered)
                if soa_sent==True:
                  is_soa_triggered = True
               
        is_call_result_triggered=False
        twilio_status = twilio_metadata.get("status", "unknown")
        call_sent=get_call_results(org_id=user_data.get("tenant_id", None),customer_number=user_data.get("customer_number"),bill_to=user_data.get("customer_name"),follow_up_date="call_2",next_action=document.get("actions", []),result_description=twilio_status,result_code=twilio_status,result_timestamp=None)
        call_result_sent=send_call_result_rpa_sharepoint(call_sent)
        if call_result_sent:
            is_call_result_triggered=True

        is_contact_triggered=False
        contact_sent=get_contact_records(org_id=user_data.get("tenant_id", None),customer_number=user_data.get("customer_number"),bill_to=user_data.get("customer_name"),contact_name_1=user_data.get("customer_number"," "),contact_name_2=user_data.get("customer_number"," "),email=user_data.get("email_address"," "),phone_country=user_data.get("phone_country"," "),phone=user_data.get("phone"," "),cell_country=user_data.get("cell_country"," "),cell_phone=user_data.get("cell_phone"," "),other_phone_country=user_data.get("other_phone_country"," "),other_phone=user_data.get("other_phone"," "),fax_country=user_data.get("fax_country"," "),fax=user_data.get("fax"," "),preferred_language=user_data.get("preferred_language"," "),contact_sequence="1")
        contact_uploaded=send_contact_rpa_sharepoint(contact_sent)
        if contact_uploaded:
            is_contact_triggered=True
        ###############################################EMAIL###################################        
        email_handler = LogicAppEmailHandler()
        document_types = []
        if is_soa_triggered:
            document_types.append("soa")
        if is_rpa_triggered:
            document_types.append("invoice_copy")
        # Check for credit memo in allowed actions
        for act in allowed_actions:
            if act.get("action_type") == "DocumentCopy":
                doc_details = act.get("document_details") or {}
                if isinstance(doc_details, dict) and doc_details.get("document_type") == "credit_memo":
                    if "credit_memo" not in document_types:
                        document_types.append("credit_memo")

        attempt_number = int(document.get("attempt_number") or 1)
        customer_name = user_data.get("customer_name", "")
        phone_number = user_data.get("phone_number", "")
        email_address = user_data.get("email_address")
        invoice_details_list = user_data.get("invoice_details") or []
        past_due_balance = invoice_details_list[0].get("outstanding_balance", 0) if invoice_details_list else 0
        try:
           past_due_balance = float(past_due_balance or 0)
        except (ValueError, TypeError):
            past_due_balance = 0.0

        call_date = datetime.now().strftime("%m/%d/%Y")
        collector_name = user_data.get("collector_name", EMAIL_SENDER_NAME)
        collector_email = user_data.get("collector_email", EMAIL_SENDER_ADDRESS)
        cc_list = [AB_BILLING_SUPPORT_EMAIL]

        # Check for escalation action
        escalation_action = None
        for act in allowed_actions:
            if act.get("action_type") == "Escalation":
                escalation_action = act
                break

        if escalation_action:
            # Escalation email to collector with CC to managers
            escalation_details = {
                "escalation_level": escalation_action.get("escalation_level", "L1"),
                "escalated_to_role": escalation_action.get("escalated_to_role", "Senior Representative"),
                "reason": escalation_action.get("reason", ""),
                "target_resolution_date": escalation_action.get("target_resolution_date", ""),
            }
            escalation_content = get_escalation_email(
                customer_name=customer_name,
                call_summary=document.get("summary", ""),
                escalation_details=escalation_details,
                sender_name=collector_name,
            )
            team_manager_email = user_data.get("team_manager_email")
            escalation_cc = ESCALATION_CC_LIST.copy()
            if team_manager_email:
                escalation_cc.append(team_manager_email)
            escalation_to = [collector_email]
            email_handler.send_email(
                to=escalation_to,
                subject=f"Escalation \u2013 {customer_name} \u2013 {call_date}",
                html_body=escalation_content,
                cc=escalation_cc,
            )

        if call_status:
            # Successful call -> send call summary email
            action_items = []
            for act in allowed_actions:
                action_items.append({
                    "action": act.get("action_type", ""),
                    "owner": customer_name,
                    "due_date": act.get("promise_to_pay_details", {}).get("promise_to_pay_date", "") if isinstance(act.get("promise_to_pay_details"), dict) else "",
                })

            dispute_info = ""
            additional_info = document.get("delay_reason", "")
            for act in allowed_actions:
                if act.get("action_type") == "Dispute":
                    details = act.get("dispute_details", {})
                    if isinstance(details, dict):
                        dispute_info = details.get("dispute", dispute_info)
            
            email_content =  get_post_call_email(
                customer_name=customer_name,
                phone_number=phone_number,
                outstanding_balance=past_due_balance,
                call_status = True,
                call_date=call_date,
                aging_details=user_data.get("aging_details", ""),
                customer_feedback=document.get("summary", ""),
                dispute_details=dispute_info,
                additional_details=additional_info,
                call_summary=document.get("summary", ""),
                action_items=action_items,
                document_types=document_types,
                sender_name=collector_name,
           )
            email_subject = f"Collections Call Summary \u2013 {customer_name} \u2013 {call_date}"
            email_content = get_post_call_summary_email(
                customer_name=customer_name,
                call_date=call_date,
                outstanding_balance=past_due_balance,
                aging_details=user_data.get("aging_details", ""),
                customer_feedback=document.get("summary", ""),
                dispute_details=dispute_info,
                additional_details=additional_info,
                action_items=action_items,
                document_types=document_types,
                sender_name=collector_name,
            )
            email_subject = f"Collections Call Summary \u2013 {customer_name} \u2013 {call_date}"
        elif attempt_number >= 2:
            # Unsuccessful call 2
            account_number = user_data.get("customer_number", "")
            email_content = get_post_call_two_email(
                ap_name=customer_name,
                account_number=account_number,
                past_due_balance=past_due_balance,
                document_types=document_types,
                sender_name=collector_name,
            )
            email_subject = "ODP Business Group \u2013 Follow-Up on Outstanding Balance"
        else:
            # Unsuccessful call 1
            email_content = get_post_call_one_email(
                ap_name=customer_name,
                phone_number=phone_number,
                past_due_balance=past_due_balance,
                sender_name=collector_name,
                document_types=document_types,
                call_status=False,
            )
            email_subject = "ODP Business Group \u2013 Outstanding Balance Notification"

        if email_address:
            email_handler.send_email([email_address], subject=email_subject, html_body=email_content, cc=cc_list) 

        
    
        # Determine extension connection status based on payload and transcript analysis
        # If an extension was supplied, verify if it was accepted or if IVR reported an invalid entry
        extension_val = user_data.get("extension") or document.get("extension") or user_data.get("phone_extension")
        extension_connected = False
        
        if extension_val and str(extension_val).strip():
            if call_status:
                invalid_phrases = [
                    "invalid entry",
                    "invalid extension",
                    "extension not recognized",
                    "extension is not valid",
                    "extension unassigned",
                    "invalid party extension",
                    "we have received an invalid entry"
                ]
                has_invalid_entry = False
                for msg in (transcript or []):
                    content_str = ""
                    if isinstance(msg, dict):
                        content_str = str(msg.get("content", "")).lower()
                    elif isinstance(msg, str):
                        content_str = msg.lower()
                    if any(phrase in content_str for phrase in invalid_phrases):
                        has_invalid_entry = True
                        break
                
                # If call completed without invalid extension errors, extension connection succeeded
                extension_connected = not has_invalid_entry
            else:
                # Call failed or was unanswered
                extension_connected = False
        else:
            # No extension was provided in the input payload
            extension_connected = False
        
        ########################################################
                # Queue records for the scheduled RPA batch process
        rpa_queue_result = process_completed_call(
            call_id=document.get("call_id") or call_sid,
            user_data=user_data,
            document=document,
            allowed_actions=allowed_actions,
            call_status=call_status,
            twilio_metadata=twilio_metadata,
            timezone_info=(
                timezone_str
                if isinstance(timezone_str, dict)
                else {}
            ),
        )

        rpa_flags = rpa_queue_result.get("flags", {})

        logger.info(
            "RPA queue result call_id=%s status=%s queued=%s failed=%s",
            document.get("call_id") or call_sid,
            rpa_queue_result.get("status"),
            rpa_queue_result.get("queued_count"),
            rpa_queue_result.get("failed_count"),
        )
        final_payload = {
            "call_id": document.get("call_id") or call_sid,
            "case_id": document.get("case_id"),
            "customer_number": document.get("customer_number") or document.get("customer_id") or document.get("user_id") or call_sid,
            "customer_name": document.get("customer_name") or document.get("user_name") or "",
            "invoice_numbers": invoice_nos,
            "recording_url": f"{WEBHOOK_URL}/audio/{document.get('call_id') or call_sid}",
            "recording": recording_info or {
                "call_id": document.get("call_id") or call_sid,
                "storage": "azure_blob",
                "container": BLOB_CONTAINER_NAME,
                "blob_name": f"{(document.get('call_id') or call_sid)}.wav"
            },
            "call_data": {
                "attempt_number": int(document.get("attempt_number") or 1),
                "call_status": call_status,
                "failure_message": failure_message or "",
                "call_start_at": str(document.get("call_data", {}).get("call_start_at") or ""),
                "call_end_at": str(document.get("call_data", {}).get("call_end_at") or ""),
                "voice_mail": user_data.get("voice_mail_detected", False)
            },
            # "local_start_time:":localized_start_time,
            # "local_end_time:":localized_end_time,
            "time_zone:":timezone_str.get("Timezone"," "), 
            "transcript": {
                "full_transcript": full_transcript_obj,
                "call_summary": [document.get("summary")] if document.get("summary") else []
            },
            "confidence_score": document.get("confidence_score"),
            "confidence_reason": document.get("confidence_reason"),
            "special_notes":document.get("special_notes"),
            #"actions":  document.get("actions") and max(document.get("actions"), key=lambda x: x["confidence_score"]) or [],
            # document.get("actions")
            "token_usage": document.get("token_usage") or {},
            "realtime_usage": realtime_usage or {},
            "telemetry_summary": telemetry_summary or {},
                 
            # "actions_items": [action for action in document.get("actions") if action.get("confidence_score", 0) >= 0.85 ] or [],
            "actions": document.get("actions", []),
            "allowed_actions": allowed_actions,
            "blocked_actions":  blocked_actions,
            "rpa_triggered": is_rpa_triggered,
             "notes_trigered":is_notes_triggered,
             "npr_triggered":is_npr_triggered,
             "dispute_triggered":is_dispute_triggered,
             "promise_to_pay_triggered":is_promise_to_pay_triggered,
            "Delay_reason":document.get("delay_reason"," "),
            "call_result_triggered":is_call_result_triggered,
            "contacts_details_triggered":is_contact_triggered,
            "soa_triggered":is_soa_triggered,
            "extension_connected": extension_connected
        }  
        print(json.dumps(final_payload["call_data"], indent=2))
        # 5. External Integration (Webhooks)
        # confidence_value=final_payload.get("confidence_score")
        # confidence_thresold=0.6
        # if confidence_value >confidence_thresold:
        # allowed_actions, blocked_actions = split_actions_by_overall_confidence(
        #     actions=document.get("actions", []),
        #     confidence_score=document.get("confidence_score", 0),
        #     confidence_reason=document.get("confidence_reason", "")
        # )
        # print(f"[Validation] Allowed Actions: {allowed_actions}")
        # print(f"[Validation] Blocked Actions: {blocked_actions}")
        logger.info(
        "[FINAL PAYLOAD] call_status=%s failure_message=%s call_id=%s extension_connected=%s",
        final_payload["call_data"]["call_status"],
       final_payload["call_data"]["failure_message"],
        final_payload["call_id"],
        final_payload["extension_connected"],
         )
        if ORCHESTRATION_LAYER and "placeholder" not in ORCHESTRATION_LAYER:
            # We remove token_usage and extension_connected for external webhook payloads (persisted in Cosmos DB only)
            external_payload = final_payload.copy()
            external_payload.pop("token_usage", None)
            external_payload.pop("realtime_usage", None)
            external_payload.pop("telemetry_summary",None)
            external_payload.pop("extension_connected", None)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(ORCHESTRATION_LAYER, json=external_payload) as response:
                        print(f"[Webhook] Report sent for {call_sid} | Status: {response.status}")
                        if response.status in [200, 201]:
                            fire_and_forget_log(user_data, "Send Call Records to Post‑Call Handler", "callhandler_e006", True)
                        else:
                            fire_and_forget_log(user_data, "Send Call Records to Post‑Call Handler", "callhandler_e006", False, "Call records could not be delivered to post‑call handler due to pipeline failure")
            except Exception as e:
                print(f"[Webhook] Failed to send report: {e}")
                fire_and_forget_log(user_data, "Send Call Records to Post‑Call Handler", "callhandler_e006", False, "Call records could not be delivered to post‑call handler due to pipeline failure")
        
        
        try:
            await save_final_payload_to_cosmos(final_payload)
        except Exception as e:
            print(f"[Cosmos] Failed to save result: {e}")
 
    except Exception as e:
        logger.exception(f"Error during reporting for {call_sid}: {e}")
        print(f"[Critical] Error during reporting for {call_sid}: {e}")
 
 
if __name__=="__main__":
   
    successful_email = get_post_call_email(
        customer_name="John Smith",
        phone_number="+1 555-123-4567",
        outstanding_balance=12500.75,
        call_status=True,
        call_date="2026-08-28",
        aging_details="31 to 60 days past due",
        customer_feedback="Payment is currently being processed.",
        dispute_details="No disputes were identified.",
        additional_details="Finance manager approval is pending.",
        call_summary=(
            "The customer confirmed receipt of the invoices and agreed "
            "to provide the ACH payment details."
        ),
        action_items=[
            {
                "action": "Provide ACH payment confirmation",
                "owner": "Customer",
                "due_date": "2026-08-31",
            },
            {
                "action": "Send Statement of Account",
                "owner": "Collections Team",
                "due_date": "2026-08-29",
            },
        ],
        document_types=[
            "invoice_copy",
            "soa",
        ],
        sender_name="Merwyn Thomas",
    )

    print(successful_email)
    email_handler1= LogicAppEmailHandler()
    email_address="merwyn.thomas@capgemini.com"
    if email_address:
            email_handler1.send_email([email_address], subject="odp_call_one", html_body=successful_email, cc="Merwyn.Thomas@theodpgroup.com") 