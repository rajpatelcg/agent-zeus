import os
from dotenv import load_dotenv


# Load environment variables once at the start
load_dotenv(override=True)

# ---------------------------------------------------------
# TWILIO CONFIGURATION
# ---------------------------------------------------------
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_CALLBACK_EVENTS = ['completed', 'failed', 'busy', 'no-answer']

# ---------------------------------------------------------
# AZURE AI SERVICES (LLM, STT, TTS)
# ---------------------------------------------------------
# Azure OpenAI (LLM)
AZURE_LLM_API_KEY = os.getenv("AZURE_API_KEY")
AZURE_LLM_ENDPOINT = os.getenv("AZURE_ENDPOINT")
ANTROPHIC_LLM_ENDPOINT=os.getenv("ANTHROPIC_ENDPOINT")

# Azure Speech Services (STT & TTS)
AZURE_SPEECH_API_KEY = os.getenv("AZURE_SPEECH_API_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION")

# ---------------------------------------------------------
# STORAGE & DATABASE
# ---------------------------------------------------------
# Azure Cosmos DB
COSMOS_ENDPOINT = os.getenv("ENDPOINT")
COSMOS_KEY = os.getenv("KEY")
DATABASE_NAME = os.getenv("DATABASE_NAME", "user-data")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "user-statements")

# Azure Blob Storage (Recordings)
BLOB_CONTAINER_NAME = (os.getenv("BLOB_CONTAINER_NAME") or "call-recordings").strip()
BLOB_KEYS = (os.getenv("BLOB_KEYS") or "").strip()
BLOB_STORAGE_ACCOUNT_NAME = (os.getenv("BLOB_STORAGE_ACCOUNT_NAME") or "").strip()
BLOB_CONTAINER_STRING = (os.getenv("BLOB_CONTAINER_STRING") or os.getenv("BLOB_CONNECTION_STRING") or "").strip()

# ---------------------------------------------------------
# NETWORKING & INTEGRATION
# ---------------------------------------------------------
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://your-ngrok-url.ngrok-free.app")
ORCHESTRATION_LAYER = os.getenv("OTHER_CONTAINER_URL")
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000

# ---------------------------------------------------------
# SHARED STATE & MANAGERS
# ---------------------------------------------------------
# 'processed_calls' tracks Call SIDs that have already been reported to prevent duplicate database entries.
# 'active_calls' is an in-memory bridge that stores metadata (customer name, invoices) from the 
# initial REST API request so the WebSocket can retrieve it once the call starts.
processed_calls = set()
active_calls = {} 

# TO MIGRATE TO REDIS:
# Replace 'active_calls = {}' with a Redis client (e.g., redis.Redis(...)).
# Use 'redis.set(call_sid, json.dumps(user_data))' in endpoints.py and 'redis.get(call_sid)' in caller.py.
# This ensures that even if the server restarts, ongoing call data is not lost.

from app.blobstorage.blob_manager import AzureBlobManager
blob_manager = AzureBlobManager()

#
#monitor
APP_INSIGHTS_CONNECTION_STRING = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or "InstrumentationKey=19e37d91-e681-41b9-bbbe-bb7287109864;IngestionEndpoint=https://eastus-8.in.applicationinsights.azure.com/;LiveEndpoint=https://eastus.livediagnostics.monitor.azure.com/;ApplicationId=95eb7b76-0ceb-42dd-9483-79dcb786b43d"

#SHAREPOINT CONFIGURATION
TENANT_ID=os.getenv("TENANT_ID")
CLIENT_ID=os.getenv("CLIENT_ID")
CLIENT_SECRET=os.getenv("CLIENT_SECRET")

HOSTNAME="officedepot.sharepoint.com"
SITE_NAME="RPA_Tasks"
RESUME_ASSISTANT_LINE = (
    "Oh, I am back. Let us continue from where we left off."
)