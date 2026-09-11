import csv
import json
import logging
import os
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from azure.core import MatchConditions
from azure.cosmos import CosmosClient, exceptions
import pandas as pd

from app.caller_features.sharepoint import upload_file_to_sharepoint
from app.config.src import COSMOS_ENDPOINT,COSMOS_KEY 

logger = logging.getLogger("rpa_batch_service")

DELIMITER = "|~"


COSMOS_DATABASE_NAME = os.getenv(

"COSMOS_DATABASE_NAME","collections",

)

COSMOS_RPA_CONTAINER_NAME = os.getenv(

"COSMOS_RPA_CONTAINER_NAME",
"rpa_outbound_queue",

)

RPA_BUSINESS_TIMEZONE = "Asia/Kolkata"


SHAREPOINT_PRODUCTION_FOLDER = "Accounts Receivable Collections/Agentic for Collections/dev/outbound"

# Invoices (document_copy) go to their own paths, split by single vs multiple invoices.
DEFAULT_OUTPUT_FOLDERS = {
    "invoice_copy_single": f"{SHAREPOINT_PRODUCTION_FOLDER}/Invoices/SingleInvoice",
    "invoice_copy_multiple": f"{SHAREPOINT_PRODUCTION_FOLDER}/Invoices/MultiInvoice",
}


RPA_RETENTION_SECONDS = int(
    os.getenv("RPA_RETENTION_SECONDS", "2592000")
)

MAX_JOB_ATTEMPTS = int(
    os.getenv("RPA_MAX_JOB_ATTEMPTS", "5")
)


_cosmos_client = CosmosClient(
    COSMOS_ENDPOINT,
    credential=COSMOS_KEY,
)

_database = _cosmos_client.get_database_client(
    COSMOS_DATABASE_NAME
)

_container = _database.get_container_client(
    COSMOS_RPA_CONTAINER_NAME
)


CSV_RECORD_TYPES = {
    "contact",
    "promise_to_pay",
    "npr",
    "dispute",
    "call_result",
    "soa",
    "special_notes",
    "document_copy",
}


FILE_PREFIXES = {
    "contact": "contact_update",
    "promise_to_pay": "promise_to_pay",
    "npr": "npr",
    "dispute": "dispute",
    "call_result": "call_result",
    "soa": "soa",
    "special_notes": "special_notes",
    "document_copy": "document_copy",
    "invoice_copy_single": "invoice_copy_single",
    "invoice_copy_multiple": "invoice_copy_multiple",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_business_date() -> str:
    business_timezone = ZoneInfo(RPA_BUSINESS_TIMEZONE)
    return datetime.now(business_timezone).strftime("%Y-%m-%d")


def sanitize_value(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, datetime):
        value = value.isoformat()

    elif isinstance(value, (dict, list, tuple)):
        value = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )

    else:
        value = str(value)

    value = (
        value
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
    )

    # Normalize repeated whitespace.
    value = " ".join(value.split())

    return value


def enqueue_rpa_record(
    record_type: str,
    payload: Dict[str, Any],
    call_id: Optional[str] = None,
    customer_number: Optional[str] = None,
    action_id: Optional[str] = None,
    business_date: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Store one outbound RPA request in Cosmos DB.

    Idempotency:
      If an idempotency_key is supplied, the same request produces
      the same Cosmos document ID and cannot be inserted twice.
    """

    if record_type not in CSV_RECORD_TYPES:
        raise ValueError(
            f"Unsupported RPA record type: {record_type}"
        )

    selected_business_date = business_date or get_business_date()

    if idempotency_key:
        document_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                idempotency_key,
            )
        )
    else:
        document_id = str(uuid.uuid4())

    now = utc_now_iso()

    document = {
        "id": document_id,
        "business_date": selected_business_date,
        "record_type": record_type,
        "status": "pending",
        "call_id": str(call_id or ""),
        "customer_number": str(customer_number or ""),
        "action_id": str(action_id or ""),
        "payload": {
            str(key): sanitize_value(value)
            for key, value in payload.items()
        },
        "attempt_count": 0,
        "last_error": "",
        "batch_file_name": "",
        "sharepoint_result": "",
        "created_at": now,
        "updated_at": now,
        "ttl": RPA_RETENTION_SECONDS,
    }

    try:
        created_item = _container.create_item(
            body=document,
        )

        logger.info(
            "Queued RPA record record_type=%s id=%s call_id=%s",
            record_type,
            document_id,
            call_id,
        )

        return created_item

    except exceptions.CosmosResourceExistsError:
        logger.info(
            "RPA request already queued id=%s idempotency_key=%s",
            document_id,
            idempotency_key,
        )

        return _container.read_item(
            item=document_id,
            partition_key=selected_business_date,
        )



# def get_pending_jobs_for_date(
#     business_date: str,
# ) -> List[Dict[str, Any]]:
#     query = """
#     SELECT c.status, COUNT(1) AS cnt
#     FROM c
#     WHERE c.business_date = @business_date
#     GROUP BY c.status
#     """

#     params = [
#     {
#         "name": "@business_date",
#         "value": "2026-09-07"
#     }
#      ]

#     for item in _container.query_items(
#     query=query,
#     parameters=params,
#     enable_cross_partition_query=True
#    ):
#      print(item)
#     return jobs

def get_pending_jobs(
    business_date: str,
) -> List[Dict[str, Any]]:
    query = """
        SELECT *
        FROM c
        WHERE c.business_date = @business_date
          AND (
                c.status = "pending"
                OR c.status = "failed"
              )
          AND c.attempt_count < @max_attempts
        ORDER BY c.created_at
    """

    parameters = [
        {
            "name": "@business_date",
            "value": business_date,
        },
        {
            "name": "@max_attempts",
            "value": MAX_JOB_ATTEMPTS,
        },
    ]

    # Cross-partition: don't assume the container's partition key is /business_date,
    # otherwise a mismatch silently returns zero rows even when matching docs exist.
    return list(
        _container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )


def get_pending_jobs1(    business_date: str,
) -> List[Dict[str, Any]]:
    query = """
      SELECT c.id,
       c.business_date,
       c.status,
       c.record_type,
       c.attempt_count
       FROM c
       """

    jobs = list(
    _container.query_items(
        query=query,
        enable_cross_partition_query=True
    )
)

    print(f"Found {len(jobs)} total records")

    for job in jobs:
       print(job)

    return jobs

def create_batch_lock(
    business_date: str,
) -> bool:
    """
    Prevent two scheduler instances from processing the same date.
    """

    lock_id = f"batch-lock-{business_date}"

    lock_document = {
        "id": lock_id,
        "business_date": business_date,
        "record_type": "batch_lock",
        "status": "processing",
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "ttl": 86400,
    }

    try:
        _container.create_item(
            body=lock_document,
        )
        return True

    except exceptions.CosmosResourceExistsError:
        logger.warning(
            "Batch lock already exists for business_date=%s",
            business_date,
        )
        return False


def delete_batch_lock(
    business_date: str,
) -> None:
    lock_id = f"batch-lock-{business_date}"

    try:
        _container.delete_item(
            item=lock_id,
            partition_key=business_date,
        )
    except exceptions.CosmosResourceNotFoundError:
        pass


def mark_job_processing(
    job: Dict[str, Any],
) -> None:
    job["status"] = "processing"
    job["attempt_count"] = int(
        job.get("attempt_count", 0)
    ) + 1
    job["updated_at"] = utc_now_iso()

    _container.replace_item(
        item=job["id"],
        body=job,
        etag=job.get("_etag"),
        match_condition=MatchConditions.IfNotModified,
    )


def mark_job_completed(
    job: Dict[str, Any],
    file_name: str,
    sharepoint_result: Any,
) -> None:
    latest_job = _container.read_item(
        item=job["id"],
        partition_key=job["business_date"],
    )

    latest_job["status"] = "completed"
    latest_job["batch_file_name"] = file_name
    latest_job["sharepoint_result"] = sanitize_value(
        sharepoint_result
    )
    latest_job["last_error"] = ""
    latest_job["completed_at"] = utc_now_iso()
    latest_job["updated_at"] = utc_now_iso()

    _container.replace_item(
        item=latest_job["id"],
        body=latest_job,
        etag=latest_job.get("_etag"),
        match_condition=MatchConditions.IfNotModified,
    )


def mark_job_failed(
    job: Dict[str, Any],
    error_message: str,
) -> None:
    try:
        latest_job = _container.read_item(
            item=job["id"],
            partition_key=job["business_date"],
        )

        latest_job["status"] = "failed"
        latest_job["last_error"] = error_message[:2000]
        latest_job["updated_at"] = utc_now_iso()

        _container.replace_item(
            item=latest_job["id"],
            body=latest_job,
            etag=latest_job.get("_etag"),
            match_condition=MatchConditions.IfNotModified,
        )

    except Exception:
        logger.exception(
            "Could not mark job as failed id=%s",
            job.get("id"),
        )


RECORD_COLUMNS = {
    "contact": [
      "org_id",
  "customer_number",
  "bill_to",
  "contact_name_1",
  "contact_name_2",
  "email",
  "phone_country",
  "phone",
  "cell_country",
  "cell_phone",
  "other_phone_country",
  "other_phone",
  "fax_country",
  "fax",
  "preferred_language",
  "contact_sequence"
    ],
    "promise_to_pay": [
        "call_id",
        "customer_number",
        "customer_name",
        "invoice_number",
        "promised_amount",
        "promised_payment_date",
        "payment_type",
        "status",
        "created_at",
    ],
    "npr": [
        "call_id",
        "customer_number",
        "customer_name",
        "reason",
        "notes",
        "status",
        "created_at",
    ],
    "dispute": [
        "call_id",
        "customer_number",
        "customer_name",
        "invoice_number",
        "disputed_amount",
        "dispute_reason",
        "status",
        "created_at",
    ],
    "call_result": [
        "org_id",
        "customer_number",
        "bill_to",
        "follow_up_date",
        "next_action",
        "created_at",
    ],
    "soa": [
        "call_id",
        "customer_number",
        "customer_name",
        "document_type",
        "delivery_method",
        "email",
        "status",
        "created_at",
    ],
    "special_notes": [
        "Organization ID",
        "Customer #",
        "Bill To #",
        "Note text",
        "Note Date",
        "Note Entered By",
        "Special Instruction Note",
    ],
    "document_copy": [
        "Bill To # - Account",
        "Document #",
        "Transaction Balance Due",
        "Contact Email Address",
        "Customer #",
    ],
    "invoice_copy_single": [
        "Invoice",
        "Email",
        "Status",
    ],
}

# Only needed where a file_label's columns don't match the payload's own keys.
FIELD_ALIASES = {
    "invoice_copy_single": {
        "Invoice": "Document #",
        "Email": "Contact Email Address",
    },
}


def build_delimited_file(
    record_type: str,
    jobs: List[Dict[str, Any]],
    business_date: str,
    file_label: Optional[str] = None,
) -> str:
    columns_key = file_label if file_label in RECORD_COLUMNS else record_type

    if columns_key not in RECORD_COLUMNS:
        raise ValueError(
            f"No column definition found for record type: {record_type}"
        )

    timestamp = datetime.now(
        ZoneInfo(RPA_BUSINESS_TIMEZONE)
    ).strftime("%Y%m%d_%H%M%S")

    file_date = business_date.replace("-", "")
    prefix = FILE_PREFIXES[file_label or record_type]
    filename = f"{prefix}_{file_date}.csv"

    output_path = os.path.join(
        tempfile.gettempdir(),
        filename,
    )

    system_id = "WC_CGOD"

    header_record = DELIMITER.join(
        [
            "*HEADER",
            system_id,
            timestamp,
        ]
    )

    columns = RECORD_COLUMNS[columns_key]
    field_aliases = FIELD_ALIASES.get(file_label, {})

    with open(
        output_path,
        mode="w",
        encoding="utf-8",
        newline="\n",
    ) as output_file:
        output_file.write(header_record + "\n")

        for job in jobs:
            job_record_type = job.get("record_type")

            if job_record_type != record_type:
                logger.warning(
                    "Skipping mismatched job id=%s expected=%s actual=%s",
                    job.get("id"),
                    record_type,
                    job_record_type,
                )
                continue

            payload = job.get("payload") or {}

            values = [
                sanitize_value(payload.get(field_aliases.get(column, column), ""))
                for column in columns
            ]

            output_file.write(
                DELIMITER.join(values) + "\n"
            )

        output_file.write("*TRAILER\n")

    logger.info(
        "Generated file record_type=%s path=%s",
        record_type,
        output_path,
    )

    return output_path


def build_excel_file(
    record_type: str,
    jobs: List[Dict[str, Any]],
    business_date: str,
    file_label: Optional[str] = None,
) -> str:
    columns_key = file_label if file_label in RECORD_COLUMNS else record_type

    if columns_key not in RECORD_COLUMNS:
        raise ValueError(
            f"No column definition found for record type: {record_type}"
        )

    file_date = business_date.replace("-", "")
    prefix = FILE_PREFIXES[file_label or record_type]
    filename = f"{prefix}_{file_date}.xlsx"

    output_path = os.path.join(
        tempfile.gettempdir(),
        filename,
    )

    columns = RECORD_COLUMNS[columns_key]
    field_aliases = FIELD_ALIASES.get(file_label, {})

    rows = [
        {
            column: sanitize_value((job.get("payload") or {}).get(field_aliases.get(column, column), ""))
            for column in columns
        }
        for job in jobs
        if job.get("record_type") == record_type
    ]

    pd.DataFrame(rows, columns=columns).to_excel(
        output_path,
        index=False,
        engine="openpyxl",
    )

    logger.info(
        "Generated file record_type=%s path=%s",
        record_type,
        output_path,
    )

    return output_path


def upload_batch_file(
    file_path: str,
    business_date: str,
    output_folder: Optional[str] = None,
) -> Any:
    file_name = os.path.basename(file_path)

    date_path = business_date.replace("-", "/")

    production_folder = (
        f"{output_folder or SHAREPOINT_PRODUCTION_FOLDER}/{date_path}"
    )

    result = upload_file_to_sharepoint(
        file_path,
        production_folder,
        file_name=file_name,
    )

    if result is None:
        raise RuntimeError(
            f"SharePoint upload returned None for {file_name}"
        )

    if result is False:
        raise RuntimeError(
            f"SharePoint upload failed for {file_name}"
        )

    return result


def process_record_type(
    record_type: str,
    jobs: List[Dict[str, Any]],
    business_date: str,
    file_label: Optional[str] = None,
    output_folder: Optional[str] = None,
    file_format: str = "csv",
) -> Dict[str, Any]:
    if not jobs:
        return {
            "record_type": file_label or record_type,
            "job_count": 0,
            "status": "skipped",
        }

    processing_jobs = []

    try:
        for job in jobs:
            mark_job_processing(job)
            processing_jobs.append(job)

        build_file = build_excel_file if file_format == "excel" else build_delimited_file

        generated_file = build_file(
            record_type,
            processing_jobs,
            business_date,
            file_label=file_label,
        )

        upload_result = upload_batch_file(
            generated_file,
            business_date,
            output_folder=output_folder,
        )

        file_name = os.path.basename(generated_file)

        for job in processing_jobs:
            mark_job_completed(
                job,
                file_name,
                upload_result,
            )

        try:
            os.remove(generated_file)
        except OSError:
            logger.warning(
                "Could not delete temporary file %s",
                generated_file,
            )

        return {
            "record_type": file_label or record_type,
            "job_count": len(processing_jobs),
            "status": "completed",
            "file_name": file_name,
        }

    except Exception as exc:
        logger.exception(
            "Batch processing failed record_type=%s",
            record_type,
        )

        for job in processing_jobs:
            mark_job_failed(
                job,
                str(exc),
            )

        return {
            "record_type": file_label or record_type,
            "job_count": len(processing_jobs),
            "status": "failed",
            "error": str(exc),
        }


def process_document_copy_jobs(
    jobs: List[Dict[str, Any]],
    business_date: str,
    output_paths: Dict[str, str],
    output_formats: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Invoices are split into a single-invoice file and a multi-invoice file,
    each uploaded to its own SharePoint path, based on how many document_copy
    jobs share the same call_id.

    output_formats picks "csv" (default) or "excel" per label, e.g.
    {"invoice_copy_single": "excel", "invoice_copy_multiple": "csv"}.
    """

    output_formats = output_formats or {}

    jobs_by_call = defaultdict(list)

    for job in jobs:
        jobs_by_call[job.get("call_id", "")].append(job)

    single_invoice_jobs = []
    multi_invoice_jobs = []

    for call_jobs in jobs_by_call.values():
        if len(call_jobs) == 1:
            single_invoice_jobs.extend(call_jobs)
        else:
            multi_invoice_jobs.extend(call_jobs)

    results = []

    if single_invoice_jobs:
        results.append(
            process_record_type(
                "document_copy",
                single_invoice_jobs,
                business_date,
                file_label="invoice_copy_single",
                output_folder=output_paths.get(
                    "invoice_copy_single",
                    DEFAULT_OUTPUT_FOLDERS["invoice_copy_single"],
                ),
                file_format=output_formats.get("invoice_copy_single", "csv"),
            )
        )

    if multi_invoice_jobs:
        results.append(
            process_record_type(
                "document_copy",
                multi_invoice_jobs,
                business_date,
                file_label="invoice_copy_multiple",
                output_folder=output_paths.get(
                    "invoice_copy_multiple",
                    DEFAULT_OUTPUT_FOLDERS["invoice_copy_multiple"],
                ),
                file_format=output_formats.get("invoice_copy_multiple", "csv"),
            )
        )

    return results


def debug_jobs_for_date(business_date: str) -> None:
    query = """
        SELECT
            c.id,
            c.business_date,
            c.status,
            c.record_type,
            c.attempt_count
        FROM c
        WHERE c.business_date = @business_date
    """

    parameters = [
        {
            "name": "@business_date",
            "value": business_date,
        }
    ]

    jobs = list(
        _container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )

    print(f"Found {len(jobs)} total records for {business_date}")

    for job in jobs:
        print(
            "id=", job.get("id"),
            "status=", repr(job.get("status")),
            "record_type=", job.get("record_type"),
            "attempt_count=", repr(job.get("attempt_count")),
        )

def get_total_outbound_count(
    business_date: str,
) -> int:
    query = """
        SELECT VALUE COUNT(1)
        FROM c
        WHERE c.business_date = @business_date
          AND c.status = "completed"
          AND IS_DEFINED(c.batch_file_name)
          AND c.batch_file_name != ""
    """

    parameters = [
        {
            "name": "@business_date",
            "value": business_date,
        }
    ]

    results = list(
        _container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )

    total_records = int(results[0]) if results else 0

    print(
        f"Total records sent to outbound "
        f"for {business_date}: {total_records}"
    )

    return total_records

def process_daily_rpa_batch(
    business_date: Optional[str] = None,
    output_paths: Optional[Dict[str, str]] = None,
    output_formats: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    output_paths lets a caller override the default SharePoint folder per
    record type, e.g. {"contact": "...", "invoice_copy_single": "..."}.

    output_formats lets a caller pick "csv" or "excel" for the invoice files,
    e.g. {"invoice_copy_single": "excel", "invoice_copy_multiple": "csv"}.
    """
    selected_date = business_date or get_business_date()
    output_paths = output_paths or {}

    if not create_batch_lock(selected_date):
        return {
            "business_date": selected_date,
            "status": "already_running",
            "results": [],
        }

    try:
        pending_jobs = get_pending_jobs(
            selected_date
        )

        if not pending_jobs:
            return {
                "business_date": selected_date,
                "status": "no_pending_jobs",
                "results": [],
            }

        grouped_jobs = defaultdict(list)

        for job in pending_jobs:
            grouped_jobs[job["record_type"]].append(job)

        results = []

        for record_type, jobs in grouped_jobs.items():
            if record_type == "document_copy":
                results.extend(
                    process_document_copy_jobs(
                        jobs,
                        selected_date,
                        output_paths,
                        output_formats,
                    )
                )
                continue

            result = process_record_type(
                record_type,
                jobs,
                selected_date,
                output_folder=output_paths.get(record_type),
            )
            results.append(result)

        final_status = (
            "completed"
            if all(
                result["status"] in {
                    "completed",
                    "skipped",
                }
                for result in results
            )
            else "partially_failed"
        )

        return {
            "business_date": selected_date,
            "status": final_status,
            "total_jobs": len(pending_jobs),
            "results": results,
        }

    finally:
       delete_batch_lock(selected_date)



if __name__ == "__main__":
    partition_key_paths = (_container.read().get("partitionKey") or {}).get("paths")
    print("Container partition key path:", partition_key_paths)

    # business_date = get_business_date()
    business_date="2026-09-09"
    # jobs = get_pending_jobs(business_date)
    # jobs =get_pending_jobs(business_date)
    jobs=debug_jobs_for_date(business_date)
    # jobs1=get_outbound_sent_count(business_date)
    summary = get_total_outbound_count(business_date)
    print("\nFinal summary:")
    print(json.dumps(summary, indent=2))
    # print(jobs)
    # print(jobs1)
    # print(f"Found {len(jobs)} jobs for business_date={business_date}")
   
    # for index, job in enumerate(jobs, start=1):
    #     print(f"\nJob {index}")
    #     print("Record type:", job.get("record_type"))
    #     print("Payload:", json.dumps(job.get("payload") or {}, indent=2, ensure_ascii=False))
    # for job in jobs[:3]:
    #    print("\nID:", job["id"])
    #    print(job.keys())
    #    print("Keys:", list(job.keys()))

    # if "payload" in job:
    #     print("Payload Keys:", list((job["payload"] or {}).keys()))
    #     print("Payload:", job["payload"])

