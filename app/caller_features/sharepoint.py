import os
from pathlib import Path

import requests
from dotenv import load_dotenv

# load_dotenv()

from app.config.src import (
    WEBHOOK_URL, 
    BLOB_CONTAINER_NAME, 
    ORCHESTRATION_LAYER, 
    processed_calls,
    account_sid, 
    auth_token,
    TENANT_ID,
    CLIENT_ID,
    CLIENT_SECRET,
    HOSTNAME,
    SITE_NAME
)
def get_access_token():
    token_url = (
        f"https://login.microsoftonline.com/"
        # f"{os.environ['TENANT_ID']}/oauth2/v2.0/token"
        f"{TENANT_ID}/oauth2/v2.0/token"
    )

    payload = {
        "grant_type": "client_credentials",
        # "client_id": os.environ["CLIENT_ID"],
        # "client_secret": os.environ["CLIENT_SECRET"],
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    }

    response = requests.post(token_url, data=payload)
    response.raise_for_status()

    return response.json()["access_token"]


def get_site_id(access_token):
    url = (
        f"https://graph.microsoft.com/v1.0/sites/"
        f"{HOSTNAME}:/sites/{SITE_NAME}"
    )

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}"
        },
    )

    response.raise_for_status()

    return response.json()["id"]


def upload_file_to_sharepoint(
    local_file_path,
    sharepoint_folder_path,
    file_name=None,
):
    """
    Upload a file to a SharePoint folder.

    Args:
        local_file_path (str): Local file path.
        sharepoint_folder_path (str): SharePoint folder path.
        file_name (str | None): Target file name in SharePoint.
                               Uses source filename if omitted.
    """

    local_path = Path(local_file_path)

    if not local_path.exists():
        raise FileNotFoundError(
            f"File not found: {local_file_path}"
        )

    access_token = get_access_token()
    site_id = get_site_id(access_token)

    target_file_name = file_name or local_path.name

    upload_url = (
        f"https://graph.microsoft.com/v1.0/sites/"
        f"{site_id}/drive/root:/"
        f"{sharepoint_folder_path}/"
        f"{target_file_name}:/content"
    )

    with open(local_path, "rb") as file_obj:
        response = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            data=file_obj,
        )

    response.raise_for_status()

    return response.json()

def list_sharepoint_folder_contents(folder_path):
    """
    List all files and folders inside a SharePoint folder.

    Args:
        folder_path (str):
            Example:
            "Accounts Receivable Collections/Agentic for Collections/Input1by1"

    Returns:
        list[dict]
    """

    access_token = get_access_token()
    site_id = get_site_id(access_token)

    url = (
        f"https://graph.microsoft.com/v1.0/sites/"
        f"{site_id}/drive/root:/{folder_path}:/children"
    )

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {access_token}"
        },
    )

    response.raise_for_status()

    return response.json().get("value", [])

def download_file_from_sharepoint(
    sharepoint_file_path,
    local_download_path=None,
):
    """
    Download a file from SharePoint.

    Args:
        sharepoint_file_path (str):
            Full path in SharePoint, for example:
            "Accounts Receivable Collections/Agentic for Collections/Input1by1/sample.xlsx"

        local_download_path (str | None):
            Local destination path.
            If omitted, saves to current directory using the SharePoint filename.

    Returns:
        pathlib.Path: Downloaded file path.
    """

    access_token = get_access_token()
    site_id = get_site_id(access_token)

    file_name = Path(sharepoint_file_path).name

    if local_download_path is None:
        local_download_path = file_name

    download_url = (
        f"https://graph.microsoft.com/v1.0/sites/"
        f"{site_id}/drive/root:/{sharepoint_file_path}:/content"
    )

    response = requests.get(
        download_url,
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        stream=True,
    )

    response.raise_for_status()

    local_path = Path(local_download_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    with open(local_path, "wb") as file_obj:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                file_obj.write(chunk)

    return local_path