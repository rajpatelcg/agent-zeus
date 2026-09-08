import os
import re
import json
import time
import hashlib
import logging
import statistics
import unicodedata
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional
from fastapi import WebSocket

# -------------------------------------------------------------------
# OPTIONAL AZURE MONITOR IMPORTS
# -------------------------------------------------------------------

try:
    from azure.monitor.opentelemetry import configure_azure_monitor
except Exception:
    configure_azure_monitor = None

try:
    from opentelemetry import trace
except Exception:
    trace = None


# -------------------------------------------------------------------
# PIPECAT IMPORTS
# -------------------------------------------------------------------

# Voice Activity Detection
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.vad.silero import SileroVADAnalyzer

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams

# Context & Frames
from pipecat.processors.aggregators.openai_llm_context import (
    OpenAILLMContext,
    OpenAILLMContextFrame,
)
from pipecat.frames.frames import TextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext

# Services & Transport
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.azure.stt import AzureSTTService
from pipecat.services.azure.tts import AzureTTSService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

# Aggregators & Turns
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
    AssistantTurnStoppedMessage,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_start import (
    VADUserTurnStartStrategy,
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.adapters.schemas.tools_schema import ToolsSchema

# Audio Processing
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.services.tts_service import TextAggregationMode


# -------------------------------------------------------------------
# APP IMPORTS
# -------------------------------------------------------------------
#from app.agents.precall_agent.call_one import get_prompt as get_prompt_one
# from app.agents.precall_agent.call_new_v3 import get_prompt as get_prompt_one
from app.agents.precall_agent.call_new_v4 import get_prompt as get_prompt_one
from app.agents.precall_agent.call_two import get_prompt as get_prompt_two

# If you have a language-aware generic get_prompt, keep this import.
# Your original code used get_prompt(user_data, language=current_language).
from app.agents.precall_agent.call_one import get_prompt

from app.caller_features.parameter import *
from app.caller_features.audit_manager import fire_and_forget_log
# from app.caller_features.call_report import report_call_result
from app.caller_features.call_report import report_call_result,notify_agent_orchestration_service
from app.caller_features.context_limit import ContextLimiterProcessor

# call cut_off
from app.caller_features.call_idle import CallIdleHandler
from app.caller_features.call_end import call_cutoff_manager
from app.caller_features.conversation_state_store import ConversationStateStore


USER_IDLE_TIMEOUT_SECONDS=10

MAX_CALL_DURATION=60*5
from app.config.src import (
    AZURE_LLM_API_KEY,
    AZURE_LLM_ENDPOINT,
    AZURE_SPEECH_API_KEY,
    AZURE_SPEECH_REGION,
    blob_manager,
    account_sid,
    auth_token,
    APP_INSIGHTS_CONNECTION_STRING,
    RESUME_ASSISTANT_LINE
)


# -------------------------------------------------------------------
# LOCAL PATHS
# -------------------------------------------------------------------

BASE_DIR = Path(".")
LOG_DIR = BASE_DIR / "logs"
# RECORDINGS_DIR = BASE_DIR / "recordings"
# TELEMETRY_DIR = BASE_DIR / "telemetry"

LOG_DIR.mkdir(exist_ok=True)
# RECORDINGS_DIR.mkdir(exist_ok=True)
# TELEMETRY_DIR.mkdir(exist_ok=True)

LOCAL_APP_LOG_FILE = LOG_DIR / "app.log"
LOCAL_EVENT_LOG_FILE = LOG_DIR / "events.jsonl"


# -------------------------------------------------------------------
# LOCAL LOGGING SETUP
# -------------------------------------------------------------------

def setup_local_logging() -> logging.Logger:
    logger_obj = logging.getLogger("pipecat-voice-agent")
    logger_obj.setLevel(logging.INFO)
    logger_obj.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOCAL_APP_LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger_obj.addHandler(console_handler)
    logger_obj.addHandler(file_handler)

    return logger_obj


logger = setup_local_logging()


# -------------------------------------------------------------------
# AZURE MONITOR TELEMETRY SETUP
# -------------------------------------------------------------------



if APP_INSIGHTS_CONNECTION_STRING and configure_azure_monitor:
    configure_azure_monitor(connection_string=APP_INSIGHTS_CONNECTION_STRING)
    logger.info("[Azure Monitor telemetry enabled]")
else:
    logger.info(
        "[Azure Monitor telemetry disabled] "
        "APPLICATIONINSIGHTS_CONNECTION_STRING not found or "
        "azure-monitor-opentelemetry unavailable. Using local logs only."
    )

tracer = trace.get_tracer("pipecat-voice-agent") if trace else None


# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.strip().lower()

    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")

    replacements = {
        "espaniol": "espanol",
        "espanõl": "espanol",
        "espanhol": "espanol",
        "spaniol": "espanol",
        "inglis": "ingles",
        "inglish": "english",
    }

    for wrong, right in replacements.items():
        text = text.replace(wrong, right)

    text = re.sub(r"[.,!?¿¡;:()\[\]{}\"']", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text




AUTO_LANGUAGES = [
   
"en-IN",
"es-ES",

]

VOICE_MAP = {
   
  "en": "en-IN-PrabhatNeural",
    "es": "es-ES-ElviraNeural",

}

MODEL_DRIFT_THRESHOLDS = {
    "avg_stt_confidence_min": 0.75,
    "avg_turn_latency_seconds_max": 8.0,
    "avg_llm_plus_tts_latency_seconds_max": 6.0,
    "avg_tts_first_audio_seconds_max": 2.5,
    "max_language_switches_per_call": 3,
    "max_error_count": 0,
}


#----------------------------------
#DETECT LANGUAGE 
#------------------------------

# Map language names / region codes / country names → ISO language code
LANGUAGE_CODE_MAP = {
    # Full language names
    "spanish": "es",
    "espanol": "es",
    "english": "en",
    "ingles": "en",
    "french": "fr",
    "frances": "fr",
    # Region codes (US territories)
    "pr": "es",       # Puerto Rico
    "hi": "es",       # Hawaii
    "gu": "es",       # Guam
    # Country names
    "puerto rico": "es",
    "hawaii": "es",
    "mexico": "es",
    "spain": "es",
    "united states": "en",
    "us": "en",
    "usa": "en",
}

# Puerto Rico postal codes: 00600–00988
# Hawaii postal codes: 96700–96898
SPANISH_POSTAL_RANGES = [
    (600, 988),        # Puerto Rico
    (96700, 96898),    # Hawaii
]


def infer_language_from_postal_code(
    postal_code: Optional[str],
    country_code: Optional[str] = None,
) -> str:
    """
    Detect language from postal code.
    Puerto Rico (00600-00988), Hawaii (96700-96898) → 'es'.
    Spain (01000-52999) → 'es'.
    Falls back to 'en'.
    """
    if country_code:
        normalized = normalize_text(country_code)
        return LANGUAGE_CODE_MAP.get(normalized, normalized)

    if not postal_code:
        return "en"

    postal = str(postal_code).strip()

    if postal.isdigit() and len(postal) == 5:
        try:
            postal_num = int(postal)

            # Check Puerto Rico and Hawaii ranges
            for range_start, range_end in SPANISH_POSTAL_RANGES:
                if range_start <= postal_num <= range_end:
                    return "es"

            # Spain postal codes
            if 1000 <= postal_num <= 52999:
                return "es"

        except ValueError:
            pass

    return "en"


def has_explicit_language(user_data: dict) -> bool:
    if not user_data:
        return False

    return bool(
        user_data.get("language")
        or user_data.get("lang")
        or user_data.get("preferred_language")
        or user_data.get("locale")
    )


def get_initial_language_from_user_data(user_data: dict) -> str:
    """
    Resolve the initial language for a call.
    Priority: explicit language field → region/country → postal code → 'en'.
    Maps full names like 'spanish' and territories like 'PR' to 'es'.
    """
    if not user_data:
        return "en"

    explicit_language = (
        user_data.get("language")
        or user_data.get("lang")
        or user_data.get("preferred_language")
        or user_data.get("locale")
    )

    if explicit_language:
        normalized = normalize_text(explicit_language)
        return LANGUAGE_CODE_MAP.get(normalized, normalized)

    country_or_region = (
        user_data.get("region")
        or user_data.get("country")
        or user_data.get("country_code")
        or user_data.get("market")
    )

    if country_or_region:
        normalized = normalize_text(country_or_region)
        return LANGUAGE_CODE_MAP.get(normalized, normalized)

    postal_code = (
        user_data.get("postal_code")
        or user_data.get("postcode")
        or user_data.get("zip_code")
        or user_data.get("zip")
    )

    return infer_language_from_postal_code(
        postal_code=postal_code,
        country_code=user_data.get("country_code") or user_data.get("country"),
    )


def detect_language_from_text(text: str) -> str:
    if not text:
        return "en"

    lower_text = text.strip().lower()

    if re.search(r"[ñáéíóúü¿¡]", lower_text):
        return "es"

    spanish_keywords = [
        "hola", "gracias", "por favor", "sí", "si", "buenos", "buenas",
        "señor", "señora", "usted", "cuenta", "pago", "pagamento",
        "dinero", "llamada", "hablar", "entiendo", "adiós",
        "hasta luego", "mañana", "ahora", "quiero", "puedo",
        "necesito", "no entiendo",
    ]

    english_keywords = [
        "hello", "hi", "thanks", "thank you", "please", "yes",
        "sir", "madam", "account", "payment", "money", "call",
        "speak", "understand", "bye", "goodbye", "today",
        "tomorrow", "now", "want", "need", "can",
    ]

    spanish_score = sum(1 for keyword in spanish_keywords if keyword in lower_text)
    english_score = sum(1 for keyword in english_keywords if keyword in lower_text)

    return "es" if spanish_score > english_score else "en"


def first_present_value(user_data: dict, keys: list[str]):
    for key in keys:
        value = user_data.get(key)
        if value not in [None, ""]:
            return key, value
    return None, None


def is_valid_email(email: Optional[str]) -> bool:
    if not email:
        return False

    email = str(email).strip()

    return bool(
        re.fullmatch(
            r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$",
            email,
        )
    )


def normalize_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""

    phone = str(phone).strip()

    if phone.startswith("+"):
        return "+" + re.sub(r"\D", "", phone[1:])

    return re.sub(r"\D", "", phone)


def is_valid_phone(phone: Optional[str]) -> bool:
    normalized = normalize_phone(phone)

    if not normalized:
        return False

    if normalized.startswith("+"):
        digits = normalized[1:]
    else:
        digits = normalized

    return digits.isdigit() and 8 <= len(digits) <= 15


def mask_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None

    email = str(email).strip()

    if "@" not in email:
        return "[INVALID_EMAIL]"

    local, domain = email.split("@", 1)

    if not local:
        return f"[EMPTY]@{domain}"

    return f"{local[:2]}***@{domain}"


def mask_phone(phone: Optional[str]) -> Optional[str]:
    normalized = normalize_phone(phone)

    if not normalized:
        return None

    if normalized.startswith("+"):
        prefix = "+"
        digits = normalized[1:]
    else:
        prefix = ""
        digits = normalized

    if len(digits) <= 4:
        return prefix + "****"

    return prefix + ("*" * max(len(digits) - 4, 0)) + digits[-4:]


def hash_optional_value(value) -> Optional[str]:
    if value in [None, ""]:
        return None

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]

def detect_language_from_text(text: str) -> str:
    """
    Lightweight English/Spanish detector.

    Returns:
        "es" for Spanish
        "en" for English/default

    Designed as a fallback for short STT transcripts.
    """

    if not text:
        return "en"

    lower_text = text.strip().lower()

    # Strong Spanish character signals
    if re.search(r"[ñáéíóúü¿¡]", lower_text):
        return "es"

    spanish_keywords = [
        "hola",
        "gracias",
        "por favor",
        "sí",
        "si",
        "buenos",
        "buenas",
        "señor",
        "señora",
        "usted",
        "cuenta",
        "pago",
        "pagamento",
        "dinero",
        "llamada",
        "hablar",
        "entiendo",
        "adiós",
        "hasta luego",
        "mañana",
        "ahora",
        "quiero",
        "puedo",
        "necesito",
        "no entiendo",
    ]

    english_keywords = [
        "hello",
        "hi",
        "thanks",
        "thank you",
        "please",
        "yes",
        "sir",
        "madam",
        "account",
        "payment",
        "money",
        "call",
        "speak",
        "understand",
        "bye",
        "goodbye",
        "today",
        "tomorrow",
        "now",
        "want",
        "need",
        "can",
    ]

    spanish_score = 0
    english_score = 0

    for keyword in spanish_keywords:
        if keyword in lower_text:
            spanish_score += 1

    for keyword in english_keywords:
        if keyword in lower_text:
            english_score += 1

    if spanish_score > english_score:
        return "es"

    return "en"



# from pipecat.processors.frame_processor import FrameProcessor


# class WebSocketDebugProcessor(FrameProcessor):
#     async def process_frame(self, frame, direction):
#         print("\n========== FRAME ==========")
#         print(type(frame))
#         print(frame)

#         await self.push_frame(frame, direction)


# -------------------------------------------------------------------
# LOCAL EVENT LOGGING
# -------------------------------------------------------------------

def write_local_event(event: dict):
    try:
        with open(LOCAL_EVENT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Failed writing local event log: {e}", exc_info=True)


def log_info(message: str, **custom_dimensions):
    payload = {
        "message": message,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **custom_dimensions,
    }

    logger.info(json.dumps(payload, ensure_ascii=False))
    write_local_event(payload)


def log_exception(message: str, exc: Exception, **custom_dimensions):
    payload = {
        "message": message,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        **custom_dimensions,
    }

    logger.error(json.dumps(payload, ensure_ascii=False), exc_info=True)
    write_local_event(payload)


# -------------------------------------------------------------------
# TELEMETRY MODELS
# -------------------------------------------------------------------

@dataclass
class TurnTelemetry:
    turn_id: int
    user_text_sample: str = ""
    assistant_text_sample: str = ""
    detected_language: Optional[str] = None
    locked_language: Optional[str] = None

    user_turn_stopped_at: Optional[float] = None
    first_bot_audio_at: Optional[float] = None
    assistant_turn_stopped_at: Optional[float] = None

    user_text_length: int = 0
    assistant_text_length: int = 0

    tts_first_audio_latency_seconds: Optional[float] = None
    llm_plus_tts_latency_seconds: Optional[float] = None
    full_turn_latency_seconds: Optional[float] = None


@dataclass
class CallTelemetry:
    session_id: str
    call_id: Optional[str]
    customer_name_hash: Optional[str]
    call_type: Optional[int]

    started_at_utc: str
    started_perf: float

    ended_at_utc: Optional[str] = None
    ended_perf: Optional[float] = None

    prompt_hash: Optional[str] = None
    prompt_chars: int = 0
    prompt_language_locked: bool = False

    total_user_audio_bytes: int = 0
    total_bot_audio_bytes: int = 0

    user_turn_count: int = 0
    assistant_turn_count: int = 0
    language_switch_count: int = 0
    current_language: Optional[str] = None

    average_stt_confidence: Optional[float] = None
    stt_confidence_processing_time_seconds: Optional[float] = None
    utterance_count: Optional[int] = None
    speech_errors_count: int = 0
    stt_confidence_values: list = field(default_factory=list)
    stt_transcript_count: int = 0

    telementary_message:str =None
    websocket_events: list = field(default_factory=list)
    #  scenario/tool 
    scenario_invoked: bool = False
    scenario_invoked_count: int = 0
    scenario_names: list = field(default_factory=list)
    tool_invoked: bool = False
    tool_invoked_count: int = 0
    tool_names: list = field(default_factory=list)
    
    errors: list = field(default_factory=list)
    missing_details: list = field(default_factory=list)
    drift_warnings: list = field(default_factory=list)
    turns: list = field(default_factory=list)
    payment_events: list = field(default_factory=list)

def extract_stt_confidence(transcription) -> Optional[float]:
    """
    Best-effort confidence extractor for Pipecat/Azure STT transcript objects.

    Returns:
        float confidence between 0 and 1 if available,
        otherwise None.

    Different Pipecat/Azure versions may expose confidence differently.
    This function safely checks common object/dict shapes.
    """

    if transcription is None:
        return None

    # Case 1: transcription is dict-like
    if isinstance(transcription, dict):
        possible_keys = [
            "confidence",
            "Confidence",
            "speech_confidence",
            "stt_confidence",
        ]

        for key in possible_keys:
            value = transcription.get(key)
            if isinstance(value, (int, float)):
                return float(value)

        # Azure sometimes nests recognition data.
        nbest = transcription.get("NBest") or transcription.get("nbest")
        if isinstance(nbest, list) and nbest:
            first = nbest[0]
            if isinstance(first, dict):
                value = first.get("Confidence") or first.get("confidence")
                if isinstance(value, (int, float)):
                    return float(value)

        return None

    # Case 2: transcription is object-like
    possible_attrs = [
        "confidence",
        "Confidence",
        "speech_confidence",
        "stt_confidence",
    ]

    for attr in possible_attrs:
        value = getattr(transcription, attr, None)
        
        print("STT transcription type:", type(transcription))
        print("STT transcription raw:", transcription)
        print("STT transcription dict:", getattr(transcription, "__dict__", None))
        if isinstance(value, (int, float)):
            return float(value)
      
    # Case 3: object has result/json/data payload
    for attr in ["result", "raw", "data", "metadata"]:
        

        if isinstance(value, dict):
            extracted = extract_stt_confidence(value)
            if extracted is not None:
                return extracted
        
    return None






class ProdTelemetryMonitor:
    def __init__(
        self,
        *,
        session_id: str,
        user_data: dict,
        initial_prompt: str,
        scenario_result: Optional[dict] = None,
    ):
        customer_name = (
            user_data.get("customer_name")
            or user_data.get("user_name")
            or "unknown"
        )

        
       
        self.call = CallTelemetry(
            session_id=session_id,
            call_id=user_data.get("call_id"),
            customer_name_hash=self._hash_value(customer_name),
            call_type=user_data.get("call_type"),
            started_at_utc=datetime.now(timezone.utc).isoformat(),
            started_perf=time.perf_counter(),
            prompt_hash=self._hash_value(initial_prompt),
            prompt_chars=len(initial_prompt or ""),
        )

        self._active_turn: Optional[TurnTelemetry] = None
        self._last_language: Optional[str] = None
        self._bot_audio_seen_for_turn = False
        self._reported_missing_details = set()

        self.emit_event(
            "call_started",
            call_id=self.call.call_id,
            call_type=self.call.call_type,
            prompt_hash=self.call.prompt_hash,
            prompt_chars=self.call.prompt_chars,
        )

    @staticmethod
    def _hash_value(value) -> str:
        if value is None:
            value = ""
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _safe_text_sample(text: str, max_len: int = 100) -> str:
        if not text:
            return ""

        text = re.sub(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", "[EMAIL]", text)
        text = re.sub(r"\b\d{7,}\b", "[NUMBER]", text)

        return text[:max_len]

    def record_websocket_event(self, event):
        self.call.websocket_events.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event
        })
    def emit_event(self, event_name: str, **dims):
        payload = {
            "event_name": event_name,
            "session_id": self.call.session_id,
            "call_id": self.call.call_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            **dims,
        }

        logger.info(json.dumps(payload, ensure_ascii=False))
        write_local_event(payload)

        if trace:
            span = trace.get_current_span()
            if span:
                for key, value in payload.items():
                    if value is not None:
                        try:
                            span.set_attribute(f"voice_agent.{key}", str(value))
                        except Exception:
                            pass

    def record_error(self, event_name: str, exc: Exception):
        error_data = {
            "event_name": event_name,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

        self.call.errors.append(error_data)

        payload = {
            "session_id": self.call.session_id,
            "call_id": self.call.call_id,
            **error_data,
        }

        logger.error(json.dumps(payload, ensure_ascii=False), exc_info=True)
        write_local_event(payload)

        if trace:
            span = trace.get_current_span()
            if span:
                try:
                    span.record_exception(exc)
                    span.set_attribute("voice_agent.error_event", event_name)
                except Exception:
                    pass

    def record_missing_detail(
        self,
        *,
        event_name: str,
        detail_name: str,
        reason: str,
        severity: str = "info",
        context: Optional[dict] = None,
        once_per_call: bool = True,
    ):
        dedupe_key = f"{event_name}:{detail_name}"

        if once_per_call and dedupe_key in self._reported_missing_details:
            return

        if once_per_call:
            self._reported_missing_details.add(dedupe_key)

        payload = {
            "event_name": event_name,
            "session_id": self.call.session_id,
            "call_id": self.call.call_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "detail_name": detail_name,
            "reason": reason,
            "severity": severity,
            "context": context or {},
        }

        self.call.missing_details.append(payload)

        logger.info(json.dumps(payload, ensure_ascii=False))
        write_local_event(payload)

        if trace:
            span = trace.get_current_span()
            if span:
                try:
                    span.set_attribute(
                        f"voice_agent.missing_detail.{detail_name}",
                        reason,
                    )
                except Exception:
                    pass
    
    
    def validate_incoming_user_data(self, user_data: dict):
        """
        Checks missing/invalid language, country_code, postal_code, email, and phone.
        All issues are stored in call.missing_details.
        """

        language_key, language_value = first_present_value(
            user_data,
            ["language", "lang", "preferred_language", "locale"],
        )

        if not language_value:
            self.record_missing_detail(
                event_name="incoming_user_data_validation",
                detail_name="language",
                reason="No explicit language field found in user_data.",
                severity="info",
                context={
                    "accepted_keys": ["language", "lang", "preferred_language", "locale"],
                },
            )
        else:
            normalized = normalize_text(language_value)
            if normalized not in {"en", "es"}:
                self.record_missing_detail(
                    event_name="incoming_user_data_validation",
                    detail_name="language",
                    reason="Language value is unsupported. Falling back to English.",
                    severity="warning",
                    context={
                        "field": language_key,
                        "value": str(language_value),
                        "supported_languages": ["en", "es"],
                    },
                )
            else:
                self.emit_event(
                    "incoming_language_verified",
                    field=language_key,
                    normalized_language=normalized,
                )

        country_key, country_value = first_present_value(
            user_data,
            ["country_code", "country", "region", "market"],
        )

        if not country_value:
            self.record_missing_detail(
                event_name="incoming_user_data_validation",
                detail_name="country_code",
                reason="No country/country_code/region/market found in user_data.",
                severity="info",
                context={
                    "accepted_keys": ["country_code", "country", "region", "market"],
                },
            )
        else:
            self.emit_event(
                "incoming_country_verified",
                field=country_key,
                country_hash=hash_optional_value(country_value),
            )

        postal_key, postal_value = first_present_value(
            user_data,
            ["postal_code", "postcode", "zip_code", "zip"],
        )

        if not postal_value:
            self.record_missing_detail(
                event_name="incoming_user_data_validation",
                detail_name="postal_code",
                reason="No postal_code/postcode/zip_code/zip found in user_data.",
                severity="info",
                context={
                    "accepted_keys": ["postal_code", "postcode", "zip_code", "zip"],
                },
            )
        else:
            self.emit_event(
                "incoming_postal_code_verified",
                field=postal_key,
                postal_code_hash=hash_optional_value(postal_value),
            )

        email_key, email_value = first_present_value(
            user_data,
            ["email", "email_address", "customer_email"],
        )

        if not email_value:
            self.call.email_valid = False
            self.record_missing_detail(
                event_name="incoming_user_data_validation",
                detail_name="email",
                reason="No email/email_address/customer_email found in user_data.",
                severity="warning",
                context={
                    "accepted_keys": ["email", "email_address", "customer_email"],
                },
            )
        elif not is_valid_email(email_value):
            self.call.email_valid = False
            self.call.email_hash = hash_optional_value(email_value)
            self.record_missing_detail(
                event_name="incoming_user_data_validation",
                detail_name="email",
                reason="Email is present but failed validation.",
                severity="warning",
                context={
                    "field": email_key,
                    "masked_email": mask_email(email_value),
                },
            )
        else:
            self.call.email_valid = True
            self.call.email_hash = hash_optional_value(email_value)
            self.emit_event(
                "incoming_email_verified",
                field=email_key,
                email_hash=self.call.email_hash,
                masked_email=mask_email(email_value),
            )

        phone_key, phone_value = first_present_value(
            user_data,
            ["phone", "phone_number", "mobile", "mobile_number", "customer_phone"],
        )

        if not phone_value:
            self.call.phone_valid = False
            self.record_missing_detail(
                event_name="incoming_user_data_validation",
                detail_name="phone",
                reason="No phone/phone_number/mobile/mobile_number/customer_phone found in user_data.",
                severity="warning",
                context={
                    "accepted_keys": [
                        "phone",
                        "phone_number",
                        "mobile",
                        "mobile_number",
                        "customer_phone",
                    ],
                },
            )
        elif not is_valid_phone(phone_value):
            self.call.phone_valid = False
            self.call.phone_hash = hash_optional_value(phone_value)
            self.record_missing_detail(
                event_name="incoming_user_data_validation",
                detail_name="phone",
                reason="Phone number is present but failed validation.",
                severity="warning",
                context={
                    "field": phone_key,
                    "masked_phone": mask_phone(phone_value),
                },
            )
        else:
            self.call.phone_valid = True
            self.call.phone_hash = hash_optional_value(normalize_phone(phone_value))
            self.emit_event(
                "incoming_phone_verified",
                field=phone_key,
                phone_hash=self.call.phone_hash,
                masked_phone=mask_phone(phone_value),
            )

    
    def on_stt_transcript(self, *, transcription=None):
        """
       R  ecords STT transcript metadata and confidence if available.
         """

        self.call.stt_transcript_count += 1

        confidence = extract_stt_confidence(transcription)

        if confidence is not None:
            self.call.stt_confidence_values.append(confidence)

            self.call.average_stt_confidence = round(
            statistics.mean(self.call.stt_confidence_values),
            4,
           )

            self.call.utterance_count = len(self.call.stt_confidence_values)

            self.emit_event(
            "stt_confidence_recorded",
            confidence=confidence,
            average_stt_confidence=self.call.average_stt_confidence,
            utterance_count=self.call.utterance_count,
           )
        else:
            self.record_missing_detail(
            event_name="stt_transcript_received",
            detail_name="stt_confidence",
            reason=(
                "STT transcript object did not expose confidence. "
                "Average STT confidence will remain null unless confidence metadata is available."
            ),
            severity="info",
            once_per_call=True,
        )

    
    def record_tool_invocation(self, *, tool_name: str):
        """
        Tracks whether any assistant tool/function was invoked.
        """

        self.call.tool_invoked = True
        self.call.tool_invoked_count += 1

        if tool_name and tool_name not in self.call.tool_names:
           self.call.tool_names.append(tool_name)

           self.emit_event(
          "tool_invoked",
           tool_name=tool_name,
           tool_invoked_count=self.call.tool_invoked_count,
           tool_names=self.call.tool_names,
          )

    
    def record_scenario_invocation(self, *, scenario_name: str):
        """
        Tracks whether a scenario-specific tool/action was invoked.
        """

        self.call.scenario_invoked = True
        self.call.scenario_invoked_count += 1

        if scenario_name and scenario_name not in self.call.scenario_names:
            self.call.scenario_names.append(scenario_name)

            self.emit_event(
           "scenario_invoked",
            scenario_name=scenario_name,
            scenario_invoked_count=self.call.scenario_invoked_count,
           scenario_names=self.call.scenario_names,
           )

    def on_user_turn_stopped(
        self,
        *,
        user_text: str,
        detected_language: Optional[str],
        locked_language: Optional[str],
    ):
        now = time.perf_counter()

        self.call.user_turn_count += 1
        turn_id = self.call.user_turn_count

        if (
            detected_language
            and self._last_language
            and detected_language != self._last_language
        ):
            self.call.language_switch_count += 1

        if detected_language:
            self._last_language = detected_language
            self.call.current_language = detected_language

        self._bot_audio_seen_for_turn = False

        self._active_turn = TurnTelemetry(
            turn_id=turn_id,
            user_text_sample=self._safe_text_sample(user_text),
            detected_language=detected_language,
            locked_language=locked_language,
            user_turn_stopped_at=now,
            user_text_length=len(user_text or ""),
        )

        self.emit_event(
            "user_turn_stopped",
            turn_id=turn_id,
            user_text_length=len(user_text or ""),
            user_text_sample=self._safe_text_sample(user_text),
            detected_language=detected_language,
            locked_language=locked_language,
            language_switch_count=self.call.language_switch_count,
        )

    def on_first_bot_audio(self):
        if not self._active_turn:
            return

        if self._bot_audio_seen_for_turn:
            return

        self._bot_audio_seen_for_turn = True

        now = time.perf_counter()
        self._active_turn.first_bot_audio_at = now

        if self._active_turn.user_turn_stopped_at:
            self._active_turn.tts_first_audio_latency_seconds = round(
                now - self._active_turn.user_turn_stopped_at,
                4,
            )

        self.emit_event(
            "tts_first_audio",
            turn_id=self._active_turn.turn_id,
            tts_first_audio_latency_seconds=(
                self._active_turn.tts_first_audio_latency_seconds
            ),
        )

    def on_assistant_turn_stopped(self, *, assistant_text: str):
        now = time.perf_counter()

        self.call.assistant_turn_count += 1

        if not self._active_turn:
            self._active_turn = TurnTelemetry(
                turn_id=self.call.assistant_turn_count,
            )

        self._active_turn.assistant_text_sample = self._safe_text_sample(
            assistant_text
        )
        self._active_turn.assistant_text_length = len(assistant_text or "")
        self._active_turn.assistant_turn_stopped_at = now

        if self._active_turn.user_turn_stopped_at:
            self._active_turn.llm_plus_tts_latency_seconds = round(
                now - self._active_turn.user_turn_stopped_at,
                4,
            )
            self._active_turn.full_turn_latency_seconds = (
                self._active_turn.llm_plus_tts_latency_seconds
            )

        self.call.turns.append(asdict(self._active_turn))

        self.emit_event(
            "assistant_turn_stopped",
            turn_id=self._active_turn.turn_id,
            assistant_text_length=len(assistant_text or ""),
            assistant_text_sample=self._safe_text_sample(assistant_text),
            llm_plus_tts_latency_seconds=(
                self._active_turn.llm_plus_tts_latency_seconds
            ),
            tts_first_audio_latency_seconds=(
                self._active_turn.tts_first_audio_latency_seconds
            ),
        )

        self._active_turn = None

    def add_audio_bytes(
        self,
        *,
        user_audio_bytes: int = 0,
        bot_audio_bytes: int = 0,
    ):
        self.call.total_user_audio_bytes += user_audio_bytes or 0
        self.call.total_bot_audio_bytes += bot_audio_bytes or 0

    def update_prompt_lock(self, *, language: str, prompt: str):
        self.call.prompt_language_locked = True
        self.call.current_language = language
        self.call.prompt_hash = self._hash_value(prompt)
        self.call.prompt_chars = len(prompt or "")

        self.emit_event(
            "prompt_language_locked",
            language=language,
            prompt_hash=self.call.prompt_hash,
            prompt_chars=self.call.prompt_chars,
        )

    def add_speech_confidence_result(
        self,
        confidence_result: dict,
        processing_time_seconds: Optional[float] = None,
    ):
        self.call.average_stt_confidence = confidence_result.get(
            "average_confidence"
        )
        self.call.utterance_count = confidence_result.get("utterance_count")
        self.call.speech_errors_count = len(confidence_result.get("errors", []))
        self.call.stt_confidence_processing_time_seconds = processing_time_seconds

        self.emit_event(
            "post_call_stt_confidence",
            average_stt_confidence=self.call.average_stt_confidence,
            utterance_count=self.call.utterance_count,
            speech_errors_count=self.call.speech_errors_count,
            stt_confidence_processing_time_seconds=processing_time_seconds,
        )

    def log_payment_event(self, event_type: str, data: dict):
        payload = {
            "event_name": event_type,
            "session_id": self.call.session_id,
            "call_id": self.call.call_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            **data,
        }

        self.call.payment_events.append(payload)

        logger.info(json.dumps(payload, ensure_ascii=False))
        write_local_event(payload)

    def _latencies(self, key: str):
        values = []

        for turn in self.call.turns:
            value = turn.get(key)
            if isinstance(value, (int, float)):
                values.append(value)

        return values

    @staticmethod
    def _p95(values):
        if not values:
            return None

        sorted_values = sorted(values)
        index = int(0.95 * (len(sorted_values) - 1))
        return sorted_values[index]

    def run_drift_checks(self):
        warnings = []

        avg_stt = self.call.average_stt_confidence

        if (
            avg_stt is not None
            and avg_stt < MODEL_DRIFT_THRESHOLDS["avg_stt_confidence_min"]
        ):
            warnings.append(
                {
                    "type": "low_stt_confidence",
                    "value": avg_stt,
                    "threshold": MODEL_DRIFT_THRESHOLDS[
                        "avg_stt_confidence_min"
                    ],
                }
            )

        turn_latencies = self._latencies("full_turn_latency_seconds")
        if turn_latencies:
            avg_turn_latency = statistics.mean(turn_latencies)

            if (
                avg_turn_latency
                > MODEL_DRIFT_THRESHOLDS["avg_turn_latency_seconds_max"]
            ):
                warnings.append(
                    {
                        "type": "high_average_turn_latency",
                        "value": round(avg_turn_latency, 4),
                        "threshold": MODEL_DRIFT_THRESHOLDS[
                            "avg_turn_latency_seconds_max"
                        ],
                    }
                )

        llm_latencies = self._latencies("llm_plus_tts_latency_seconds")
        if llm_latencies:
            avg_llm_latency = statistics.mean(llm_latencies)

            if (
                avg_llm_latency
                > MODEL_DRIFT_THRESHOLDS[
                    "avg_llm_plus_tts_latency_seconds_max"
                ]
            ):
                warnings.append(
                    {
                        "type": "high_llm_plus_tts_latency",
                        "value": round(avg_llm_latency, 4),
                        "threshold": MODEL_DRIFT_THRESHOLDS[
                            "avg_llm_plus_tts_latency_seconds_max"
                        ],
                    }
                )

        tts_latencies = self._latencies("tts_first_audio_latency_seconds")
        if tts_latencies:
            avg_tts_latency = statistics.mean(tts_latencies)

            if (
                avg_tts_latency
                > MODEL_DRIFT_THRESHOLDS["avg_tts_first_audio_seconds_max"]
            ):
                warnings.append(
                    {
                        "type": "high_tts_first_audio_latency",
                        "value": round(avg_tts_latency, 4),
                        "threshold": MODEL_DRIFT_THRESHOLDS[
                            "avg_tts_first_audio_seconds_max"
                        ],
                    }
                )

        if (
            self.call.language_switch_count
            > MODEL_DRIFT_THRESHOLDS["max_language_switches_per_call"]
        ):
            warnings.append(
                {
                    "type": "excessive_language_switching",
                    "value": self.call.language_switch_count,
                    "threshold": MODEL_DRIFT_THRESHOLDS[
                        "max_language_switches_per_call"
                    ],
                }
            )

        if len(self.call.errors) > MODEL_DRIFT_THRESHOLDS["max_error_count"]:
            warnings.append(
                {
                    "type": "runtime_errors_detected",
                    "value": len(self.call.errors),
                    "threshold": MODEL_DRIFT_THRESHOLDS[
                        "max_error_count"
                    ],
                }
            )

        self.call.drift_warnings = warnings

        for warning in warnings:
            self.emit_event("model_drift_warning", **warning)

        return warnings

    def finalize(self):
        self.call.ended_at_utc = datetime.now(timezone.utc).isoformat()
        self.call.ended_perf = time.perf_counter()

        call_duration_seconds = round(
            self.call.ended_perf - self.call.started_perf,
            4,
        )

        self.run_drift_checks()

        turn_latencies = self._latencies("full_turn_latency_seconds")
        llm_latencies = self._latencies("llm_plus_tts_latency_seconds")
        tts_latencies = self._latencies("tts_first_audio_latency_seconds")

        summary = {
            "session_id": self.call.session_id,
            "call_id": self.call.call_id,
            "call_type": self.call.call_type,
            "call_duration_seconds": call_duration_seconds,
            "user_turn_count": self.call.user_turn_count,
            "assistant_turn_count": self.call.assistant_turn_count,
            "language_switch_count": self.call.language_switch_count,
            "current_language": self.call.current_language,
            "average_stt_confidence": self.call.average_stt_confidence,
            "stt_confidence_processing_time_seconds": (
                self.call.stt_confidence_processing_time_seconds
            ),
            "utterance_count": self.call.utterance_count,
            "speech_errors_count": self.call.speech_errors_count,
            "total_user_audio_bytes": self.call.total_user_audio_bytes,
            "total_bot_audio_bytes": self.call.total_bot_audio_bytes,
            "avg_turn_latency_seconds": (
                round(statistics.mean(turn_latencies), 4)
                if turn_latencies
                else None
            ),
            "p95_turn_latency_seconds": (
                round(self._p95(turn_latencies), 4)
                if turn_latencies
                else None
            ),
            "avg_llm_plus_tts_latency_seconds": (
                round(statistics.mean(llm_latencies), 4)
                if llm_latencies
                else None
            ),
            "avg_tts_first_audio_latency_seconds": (
                round(statistics.mean(tts_latencies), 4)
                if tts_latencies
                else None
            ),
            "drift_warning_count": len(self.call.drift_warnings),
            "error_count": len(self.call.errors),
            "missing_detail_count": len(self.call.missing_details),
            "prompt_hash": self.call.prompt_hash,
            "prompt_chars": self.call.prompt_chars,
            "prompt_language_locked": self.call.prompt_language_locked,
            
            "stt_transcript_count": self.call.stt_transcript_count,
            "stt_confidence_sample_count": len(self.call.stt_confidence_values),

            "scenario_invoked": self.call.scenario_invoked,
            "scenario_invoked_count": self.call.scenario_invoked_count,
            "scenario_names": self.call.scenario_names,

            "tool_invoked": self.call.tool_invoked,
            "tool_invoked_count": self.call.tool_invoked_count,
            "tool_names": self.call.tool_names,
            "websocket_events": self.call.websocket_events,

        }

        self.emit_event("call_completed", **summary)

        # output_path = TELEMETRY_DIR / f"{self.call.session_id}_telemetry.json"

        # with open(output_path, "w", encoding="utf-8") as f:
        #     json.dump(
        #         {
        #             "summary": summary,
        #             "call": asdict(self.call),
        #         },
        #         f,
        #         indent=2,
        #         ensure_ascii=False,
        #     )

        return summary


# -------------------------------------------------------------------
# MAIN VOICE AGENT HANDLER
# -------------------------------------------------------------------


async def handle_voice_agent(
    websocket_client: WebSocket,
    stream_sid: str,
    call_sid: str,
    user_data: dict = None,
):
    """
    Configures and executes the Pipecat real-time voice pipeline
    with Azure Monitor telemetry fallback to local logging.

    Stateful behavior:
        - If user_data["call_sid"] is present, load conversation state from Cosmos DB.
        - If user_data["call_sid"] is not present, treat this as a fresh call.
        - State is saved using user_data["call_sid"] when available, otherwise runtime call_sid.
    """

    if user_data is None:
        user_data = {}

    telemetry: Optional[ProdTelemetryMonitor] = None

    conversation_state_store = ConversationStateStore()
    resumed_state  = await conversation_state_store.load_state(
                call_sid=call_sid,
            )
    should_attempt_resume = (
        resumed_state is not None
        and resumed_state.get("messages")
        and len(resumed_state.get("messages")) > 0)
    resume_messages = []
    is_resumed_call = False
    resumed_language = None
    resumed_language_locked = False

    if should_attempt_resume:
        try:
            if resumed_state and resumed_state.get("messages"):
                resume_messages = resumed_state.get("messages", [])
                is_resumed_call = True
                resumed_language = resumed_state.get("current_language")
                resumed_language_locked = bool(
                    resumed_state.get("language_locked")
                )

                log_info(
                    "conversation_state_loaded",
                    call_sid=call_sid,
                    stream_sid=stream_sid,
                    resumed_message_count=len(resume_messages),
                    resumed_language=resumed_language,
                    resumed_language_locked=resumed_language_locked
                )
            else:
                log_info(
                    "conversation_state_empty_or_not_found",
                    call_sid=call_sid,
                    stream_sid=stream_sid
                )

        except Exception as e:
            log_exception(
                "conversation_state_load_failed",
                e,
                call_sid=call_sid,
                stream_sid=stream_sid
            )
    else:
        log_info(
            "conversation_state_resume_skipped",
            reason="user_data.call_sid_not_present",
            call_sid=call_sid,
            stream_sid=stream_sid
        )

    log_info(
        "handle_voice_agent_started",
        call_sid=call_sid,
        stream_sid=stream_sid,
        user_call_id=user_data.get("call_id"),
        call_type=user_data.get("call_type"),
        should_attempt_resume=should_attempt_resume,
        is_resumed_call=is_resumed_call
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=TwilioFrameSerializer(
                stream_sid,
                call_sid=call_sid,
                account_sid=account_sid,
                auth_token=auth_token,
                params=TwilioFrameSerializer.InputParams(
                    auto_hang_up=True,
                ),
            ),
        ),
    )

    llm = AzureLLMService(
        api_key=AZURE_LLM_API_KEY,
        model=AZURE_DEPLOYMENT,
        api_version=LLM_MODEL_VERSION,
        endpoint=AZURE_LLM_ENDPOINT,
    )

    stt = AzureSTTService(
        api_key=AZURE_SPEECH_API_KEY,
        region=AZURE_SPEECH_REGION,
        auto_detect_source_languages=AUTO_LANGUAGES,
    )

    turn_end_time = 0
    start_silence_time = 0

    tts = AzureTTSService(
        api_key=AZURE_SPEECH_API_KEY,
        region=AZURE_SPEECH_REGION,
        voice=TTS_VOICE,
        text_aggregation_mode=TextAggregationMode.SENTENCE,
        settings=AzureTTSService.Settings(
            style=VOICE_STYLE,
            rate=VOICE_RATE,
        ),
    )

    call_type = user_data.get("call_type")

    try:
        system_prompt = (
            get_prompt_one(user_data)
            if call_type == 1
            else get_prompt_two(user_data)
        )

        if is_resumed_call:
            system_prompt = (
                system_prompt
                + "\n\n"
                + "This is a resumed phone call. Previous conversation messages "
                + "are already included in the context. Start your next assistant "
                + f"response with this exact sentence: '{RESUME_ASSISTANT_LINE}' "
                + "Then continue naturally from the last unresolved point. "
                + "Do not restart the call script unless the customer asks for it."
            )

        fire_and_forget_log(
            user_data,
            "Call Script Generation",
            "callhandler_e002",
            True,
        )

    except Exception as e:
        fire_and_forget_log(
            user_data,
            "Call Script Generation",
            "callhandler_e002",
            False,
            "AI call script generation failed due to configuration or model error",
        )
        log_exception(
            "call_script_generation_failed",
            e,
            call_sid=call_sid,
            stream_sid=stream_sid,
        )
        raise

    telemetry = ProdTelemetryMonitor(
        session_id=call_sid,
        user_data=user_data,
        initial_prompt=system_prompt,
    )

    initial_context_messages = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]

    if resume_messages:
        for msg in resume_messages:
            role = msg.get("role")
            content = msg.get("content")

            if role in {"user", "assistant"} and content:
                initial_context_messages.append(
                    {
                        "role": role,
                        "content": content,
                    }
                )

    context = OpenAILLMContext(initial_context_messages)

    context1 = LLMContext(initial_context_messages)

    smart_turn = LocalSmartTurnAnalyzerV3(
        params=SmartTurnParams(stop_secs=0.5)
    )

    vad_analyzer = SileroVADAnalyzer(
        params=VADParams(
            stop_secs=VAD_STOP_DURATION,
            min_speech_secs=VAD_MIN_SPEECH_DURATION,
        )
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad_analyzer,
            user_idle_timeout=USER_IDLE_TIMEOUT_SECONDS,
            user_turn_strategies=UserTurnStrategies(
                start=[
                    VADUserTurnStartStrategy(),
                    TranscriptionUserTurnStartStrategy(),
                ],
                stop=[
                    TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=smart_turn,
                    )
                ],
            ),
        ),
    )

    context_limiter = ContextLimiterProcessor(
        max_messages=MAX_CONTEXT_MESSAGES
    )

    record_id = user_data.get("call_id") or call_sid

    audiobuffer = AudioBufferProcessor(
        num_channels=NUM_CHANNELS,
        enable_turn_audio=False,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            context_limiter,
            llm,
            tts,
            transport.output(),
            audiobuffer,
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            allow_interruptions=ALLOW_INTERRUPTIONS,
        ),
    )

    idle_handler = CallIdleHandler(context=context, task=task)

    current_language = resumed_language or "en"
    language_locked = resumed_language_locked
    _last_user_text: list[str] = [""]

    async def persist_conversation_state(
        *,
        reason: str,
        call_success: Optional[bool] = None,
        failure_message: Optional[str] = None,
    ):
        try:
            await conversation_state_store.save_state(
                call_sid=call_sid,
                user_data=user_data,
                messages=context.get_messages(),
                current_language=current_language,
                language_locked=language_locked,
                call_success=call_success,
                failure_message=failure_message,
                reason=reason
            )

        except Exception as e:
            log_exception(
                "conversation_state_persist_failed",
                e,
                call_sid=call_sid,
                stream_sid=stream_sid,
                reason=reason
            )

    @stt.event_handler("on_transcript")
    async def on_stt_transcript(service, transcription):
        nonlocal turn_end_time
        nonlocal start_silence_time

        turn_end_time = time.time()

        latency_since_silence = time.time() - start_silence_time

        if telemetry:
            telemetry.on_stt_transcript(transcription=transcription)

            telemetry.emit_event(
                "stt_transcript_received",
                latency_since_silence_seconds=round(latency_since_silence, 4),
            )

        log_info(
            "stt_transcript_received",
            call_sid=call_sid,
            stream_sid=stream_sid,
            latency_since_silence_seconds=round(latency_since_silence, 4),
            average_stt_confidence=(
                telemetry.call.average_stt_confidence if telemetry else None
            ),
        )

        print(
            "[Latency] STT Result Segment received. "
            f"Time since silence: {latency_since_silence:.3f}s"
        )

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        try:
            if telemetry:
                telemetry.add_audio_bytes(
                    user_audio_bytes=len(audio or b"")
                )

            await blob_manager.upload_chunk(record_id, audio)

        except Exception as e:
            if telemetry:
                telemetry.record_error("blob_audio_upload_failed", e)

            fire_and_forget_log(
                user_data,
                "Blob Audio Recording",
                "callhandler_e007",
                False,
                "Audio recording failed during upload chunk",
            )

            raise

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        idle_handler.reset()

        if telemetry:
            telemetry.emit_event(
                "user_turn_started",
            )

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(
        aggregator,
        strategy,
        message: UserTurnStoppedMessage,
    ):
        nonlocal start_silence_time
        nonlocal current_language
        nonlocal language_locked
        nonlocal context

        start_silence_time = time.time()

        user_text = message.content or ""
        _last_user_text[0] = user_text

        print(f"Conversation - User: {user_text}")

        detected_language = current_language

        idle_handler.reset()

        try:
            if not language_locked:
                detected_language = detect_language_from_text(user_text)

                if detected_language != current_language:
                    current_language = detected_language
                    language_locked = True

                    print(
                        "[Language] Detected from user speech: "
                        f"{current_language}"
                    )

                    try:
                        new_system_prompt = get_prompt(
                            user_data,
                            language=current_language,
                        )

                        if is_resumed_call:
                            new_system_prompt = (
                                new_system_prompt
                                + "\n\n"
                                + "This is a resumed phone call. Continue from "
                                + "the previous context and do not restart the script."
                            )

                    except TypeError:
                        new_system_prompt = system_prompt

                        if telemetry:
                            telemetry.record_missing_detail(
                                event_name="prompt_language_lock",
                                detail_name="language_specific_prompt",
                                reason=(
                                    "get_prompt(user_data, language=...) "
                                    "is not available. Reusing initial prompt."
                                ),
                                severity="warning",
                                context={
                                    "requested_language": current_language,
                                },
                            )

                    preserved_messages = [
                        msg
                        for msg in context.get_messages()
                        if msg.get("role") in {"user", "assistant"}
                        and msg.get("content")
                    ]

                    context.messages = [
                        {
                            "role": "system",
                            "content": new_system_prompt,
                        },
                        *preserved_messages,
                    ]

                    if telemetry:
                        telemetry.update_prompt_lock(
                            language=current_language,
                            prompt=new_system_prompt,
                        )

            if telemetry:
                telemetry.on_user_turn_stopped(
                    user_text=user_text,
                    detected_language=detected_language,
                    locked_language=(
                        current_language if language_locked else None
                    ),
                )

            await persist_conversation_state(reason="user_turn_stopped")

        except Exception as e:
            if telemetry:
                telemetry.record_error("user_turn_processing_failed", e)
            raise

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        if telemetry:
            telemetry.emit_event(
                "user_turn_idle",
                last_user_text=(
                    _last_user_text[0][:100]
                    if _last_user_text[0]
                    else ""
                ),
            )

        await persist_conversation_state(reason="user_turn_idle")
        await idle_handler.handle()

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(
        aggregator,
        message: AssistantTurnStoppedMessage,
    ):
        try:
            if message.content:
                assistant_latency = time.time() - turn_end_time

                print(
                    f"Conversation - Assistant: {message.content} "
                    f"| TTFT: {assistant_latency:.3f}s"
                )

                if telemetry:
                    telemetry.on_assistant_turn_stopped(
                        assistant_text=message.content,
                    )

                    telemetry.emit_event(
                        "assistant_ttft",
                        ttft_seconds=round(assistant_latency, 4),
                    )

                await persist_conversation_state(
                    reason="assistant_turn_stopped"
                )

            if hasattr(message, "tool_calls") and message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call["function"]["name"]

                    print(
                        "Conversation - Assistant Tool Call: "
                        f"{tool_name}"
                    )

                    if telemetry:
                        telemetry.record_tool_invocation(
                            tool_name=tool_name,
                        )

                        scenario_tool_names = {
                            "escalation",
                            "Partial Payment",
                            "payment_due",
                            "email",
                            "dispute",
                            "npr",
                            "unknown",
                        }

                        if (
                            "scenario" in tool_name.lower()
                            or tool_name in scenario_tool_names
                        ):
                            telemetry.record_scenario_invocation(
                                scenario_name=tool_name,
                            )

                    await persist_conversation_state(
                        reason="tool_or_scenario_invoked"
                    )

        except Exception as e:
            if telemetry:
                telemetry.record_error(
                    "assistant_turn_processing_failed",
                    e,
                )
            raise

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        log_info(
            "client_connected",
            call_sid=call_sid,
            stream_sid=stream_sid,
            should_attempt_resume=should_attempt_resume,
            is_resumed_call=is_resumed_call
        )

        if telemetry:
            telemetry.emit_event(
                "client_connected",
                stream_sid=stream_sid,
                should_attempt_resume=should_attempt_resume,
                is_resumed_call=is_resumed_call
            )

        await audiobuffer.start_recording()
        await task.queue_frames([OpenAILLMContextFrame(context)])

        call_cutoff_manager.set_queue_frame_callback(task.queue_frames)

        await call_cutoff_manager.start_call_timer(
            call_sid,
            duration_seconds=MAX_CALL_DURATION,
        )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        log_info(
            "client_disconnected",
            call_sid=call_sid,
            stream_sid=stream_sid,
        )

        if telemetry:
            telemetry.emit_event(
                "client_disconnected",
                stream_sid=stream_sid,
            )

        await persist_conversation_state(reason="client_disconnected")

        await audiobuffer.stop_recording()
        await task.cancel()

    call_success = True
    failure_msg = None

    try:
        if tracer:
            with tracer.start_as_current_span("voice_agent_call") as span:
                try:
                    span.set_attribute("voice_agent.call_sid", call_sid)
                    span.set_attribute("voice_agent.stream_sid", stream_sid)
                    span.set_attribute(
                        "voice_agent.call_type",
                        str(call_type),
                    )
                    span.set_attribute(
                        "voice_agent.should_attempt_resume",
                        str(should_attempt_resume),
                    )
                    span.set_attribute(
                        "voice_agent.is_resumed_call",
                        str(is_resumed_call),
                    )
                except Exception:
                    pass

                runner = PipelineRunner(
                    handle_sigint=False,
                    force_gc=FORCE_GC_ON_RUN,
                )
                await runner.run(task)
        else:
            runner = PipelineRunner(
                handle_sigint=False,
                force_gc=FORCE_GC_ON_RUN,
            )
            await runner.run(task)

    except Exception as e:
        print(f"Error during call: {e}")

        call_success = False
        failure_msg = f"Server crashed or error during call: {str(e)}"

        if telemetry:
            telemetry.record_error("voice_agent_call_failed", e)

        log_exception(
            "voice_agent_call_failed",
            e,
            call_sid=call_sid,
            stream_sid=stream_sid,
        )

    await call_cutoff_manager.cancel_timer(call_sid)

    if idle_handler.triggered:
        call_success = False
        failure_msg = "Call disconnected due to user inactivity (idle)."

    elif call_cutoff_manager.has_exceeded(call_sid):
        call_success = False
        failure_msg = (
            f"Call exceeded maximum allowed duration of "
            f"{MAX_CALL_DURATION} seconds."
        )

    full_transcript = [
        msg
        for msg in context.get_messages()
        if msg.get("role") in ["user", "assistant"]
        and msg.get("content")
    ]

    try:
        recording_info = await blob_manager.finalize_recording(
            record_id,
            SAMPLE_RATE,
            NUM_CHANNELS,
        )

        fire_and_forget_log(
            user_data,
            "Blob Audio Recording",
            "callhandler_e007",
            True,
        )

        if telemetry:
            telemetry.emit_event(
                "recording_finalized",
                recording_available=bool(recording_info),
            )

    except Exception as e:
        fire_and_forget_log(
            user_data,
            "Blob Audio Recording",
            "callhandler_e007",
            False,
            "Audio recording failed during finalization",
        )

        if telemetry:
            telemetry.record_error("blob_audio_finalization_failed", e)

        log_exception(
            "blob_audio_finalization_failed",
            e,
            call_sid=call_sid,
            record_id=record_id,
        )

        recording_info = None

    telemetry_summary = None

    try:
        if telemetry:
            telemetry_summary = telemetry.finalize()

    except Exception as e:
        log_exception(
            "telemetry_finalize_failed",
            e,
            call_sid=call_sid,
            stream_sid=stream_sid,
        )

    await persist_conversation_state(
        reason="call_finalized",
        call_success=call_success,
        failure_message=failure_msg,
    )

    try:
        notify_agent_orchestration_service(user_data.get("call_id", " "))
        logging.info(full_transcript)

        await report_call_result(
            call_sid,
            user_data,
            full_transcript,
            call_status=call_success,
            recording_info=recording_info,
            failure_message=failure_msg,
            telemetry_summary=telemetry_summary,
        )

        call_cutoff_manager.cleanup_call(call_sid)

        log_info(
            "call_report_completed",
            call_sid=call_sid,
            stream_sid=stream_sid,
            call_success=call_success,
            telemetry_summary=telemetry_summary,
        )

        if telemetry:
            telemetry.emit_event(
                "call_report_completed",
                call_success=call_success,
            )

    except Exception as e:
        if telemetry:
            telemetry.record_error("call_report_failed", e)

        log_exception(
            "call_report_failed",
            e,
            call_sid=call_sid,
            stream_sid=stream_sid,
        )

        raise

    finally:
        await conversation_state_store.close()

    return {
        "call_sid": call_sid,
        "stream_sid": stream_sid,
        "call_success": call_success,
        "failure_message": failure_msg,
        "should_attempt_resume": should_attempt_resume,
        "is_resumed_call": is_resumed_call,
        "telemetry_summary": telemetry_summary,
    }