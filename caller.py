import asyncio
import re
import time
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

from fastapi import WebSocket
from twilio.rest import Client

# -------------------------------------------------------------------
# PIPECAT IMPORTS
# -------------------------------------------------------------------
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.aggregators.openai_llm_context import (
    OpenAILLMContext,
    OpenAILLMContextFrame,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.azure.stt import AzureSTTService
from pipecat.services.azure.tts import AzureTTSService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
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
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.services.tts_service import TextAggregationMode

# -------------------------------------------------------------------
# APP IMPORTS
# -------------------------------------------------------------------
# from app.agents.precall_agent.call_langgraph_cosmos import get_prompt as get_prompt_one
# from app.agents.precall_agent.call_two_langgraph_cosmos import get_prompt as get_prompt_two
from app.agents.precall_agent.call_one import get_prompt
from app.agents.precall_agent.call_staged_one import get_prompt as get_prompt_one
from app.agents.precall_agent.call_staged_two import get_prompt as get_prompt_two

from app.caller_features.parameter import *
from app.caller_features.audit_manager import fire_and_forget_log
# from app.caller_features.call_reo import report_call_result, notify_agent_orchestration_service
from app.caller_features.call_report import report_call_result, notify_agent_orchestration_service
from app.caller_features.context_limit import ContextLimiterProcessor
from app.caller_features.dtmf import resolve_extension_digits
from app.caller_features.call_idle import CallIdleHandler
from app.caller_features.call_end import call_cutoff_manager
from app.config.src import (
    AZURE_LLM_API_KEY,
    AZURE_LLM_ENDPOINT,
    AZURE_SPEECH_API_KEY,
    AZURE_SPEECH_REGION,
    blob_manager,
    account_sid,
    auth_token,
    WEBHOOK_URL,
)

USER_IDLE_TIMEOUT_SECONDS = 10
MAX_CALL_DURATION = 60 * 10


# -------------------------------------------------------------------
# LOGGING
# -------------------------------------------------------------------
LOG_DIR = Path(".") / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("pipecat-voice-agent")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    _console = logging.StreamHandler()
    _console.setFormatter(_formatter)
    logger.addHandler(_console)

    _file = RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    _file.setFormatter(_formatter)
    logger.addHandler(_file)


# -------------------------------------------------------------------
# LANGUAGE DETECTION
# -------------------------------------------------------------------
def detect_language_from_text(text: str) -> str:
    """Lightweight English/Spanish detector for short STT transcripts."""
    if not text:
        return "en"

    lower_text = text.strip().lower()

    if re.search(r"[ñáéíóúü¿¡]", lower_text):
        return "es"

    spanish_keywords = [
        "hola", "gracias", "por favor", "sí", "si", "buenos", "buenas",
        "señor", "señora", "usted", "cuenta", "pago", "dinero", "llamada",
        "hablar", "entiendo", "adiós", "mañana", "ahora", "quiero", "puedo",
        "necesito", "no entiendo",
    ]
    english_keywords = [
        "hello", "hi", "thanks", "thank you", "please", "yes", "sir", "madam",
        "account", "payment", "money", "call", "speak", "understand", "bye",
        "goodbye", "today", "tomorrow", "now", "want", "need", "can",
    ]

    spanish_score = sum(1 for k in spanish_keywords if k in lower_text)
    english_score = sum(1 for k in english_keywords if k in lower_text)

    return "es" if spanish_score > english_score else "en"


# -------------------------------------------------------------------
# REFERENCE / CHECK NUMBER CAPTURE
# -------------------------------------------------------------------
# Matches phrases like "reference number 12345", "check no. 9981",
# "confirmation # AB-1234", "transaction id 7788".
REFERENCE_NUMBER_PATTERN = re.compile(
    r"(?i)\b(reference|ref|confirmation|check|cheque|transaction|payment)\s*"
    r"(?:number|num|no\.?|#|id)?\s*(?:is|:|-|of)?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-]{3,})"
)


def extract_reference_or_check_number(text: str) -> Optional[dict]:
    """
    Extract a payment reference or check number from user speech.
    Returns {"type": <label>, "value": <number>} or None.
    """
    if not text:
        return None

    match = REFERENCE_NUMBER_PATTERN.search(text)
    if not match:
        return None

    label = match.group(1).lower()
    value = match.group(2).strip().strip("-")

    if not value:
        return None

    ref_type = "check_number" if label in {"check", "cheque"} else "reference_number"
    return {"type": ref_type, "value": value}


def capture_reference_number(user_data: dict, user_text: str) -> None:
    """Store any reference/check number spoken by the user for post-call notes."""
    found = extract_reference_or_check_number(user_text)
    if not found:
        return

    user_data[found["type"]] = found["value"]

    note_line = f"{found['type'].replace('_', ' ').title()}: {found['value']}"
    existing_note = (user_data.get("note") or "").strip()

    if found["value"] not in existing_note:
        user_data["note"] = f"{existing_note} {note_line}".strip()

    logger.info("Captured %s = %s", found["type"], found["value"])


# -------------------------------------------------------------------
# PCI REDACTION (keep card data out of transcripts; preserve ref/check numbers)
# -------------------------------------------------------------------
CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[\s-]?){13,19}(?!\d)")
CVV_PATTERN = re.compile(
    r"(?i)\b(cvv|cvc|security\s*code|card\s*code)\s*(?:is|:|-)?\s*\d{3,4}\b"
)
EXPIRY_PATTERN = re.compile(
    r"(?i)\b(expiry|expiration|expires|exp)\s*(?:date|is|:|-)?\s*"
    r"(?:0[1-9]|1[0-2])\s*[/\-]\s*(?:\d{2}|\d{4})\b"
)


def passes_luhn_check(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False

    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def mask_card_candidate(match: re.Match) -> str:
    original = match.group(0)
    digits = re.sub(r"\D", "", original)
    # Only real card numbers (Luhn-valid) are masked; check/reference numbers pass through.
    if not passes_luhn_check(digits):
        return original
    return f"[CARD_NUMBER_ENDING_{digits[-4:]}]"


def redact_sensitive_text(text: Optional[str]) -> str:
    if not text:
        return ""

    sanitized = str(text)
    sanitized = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", sanitized)
    sanitized = CARD_CANDIDATE_PATTERN.sub(mask_card_candidate, sanitized)
    sanitized = CVV_PATTERN.sub(lambda m: f"{m.group(1)} [REDACTED]", sanitized)
    sanitized = EXPIRY_PATTERN.sub(lambda m: f"{m.group(1)} [REDACTED]", sanitized)
    return sanitized


# -------------------------------------------------------------------
# EXTENSION DTMF HELPERS
# -------------------------------------------------------------------
def build_extension_prompt_timing_twiml(extension_digits: str) -> str:
    ws_redirect_url = f"{WEBHOOK_URL}/api/"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Play digits="ww{extension_digits}"></Play>'
        f'<Redirect method="POST">{ws_redirect_url}</Redirect>'
        '</Response>'
    )


def should_enter_extension_now(user_text: str) -> bool:
    lower_text = user_text.lower()
    triggers = [
        "digit extension", "enter it now", "enter your extension",
        "enter party extension", "enter the extension",
        "parties 4 digit extension", "party's 4 digit extension",
        "please enter it now", "if you know your party", "extension, please enter",
    ]
    return any(trigger in lower_text for trigger in triggers)


def is_invalid_extension_retry_prompt(user_text: str) -> bool:
    lower_text = user_text.lower()
    invalid_prompts = ["invalid entry", "invalid extension", "did not get that", "try again"]
    return any(prompt in lower_text for prompt in invalid_prompts)


async def enter_extension_after_prompt(call_sid: str, user_data: dict, user_text: str) -> bool:
    extension_val = user_data.get("extension") or user_data.get("phone_extension")
    if not extension_val or not should_enter_extension_now(user_text):
        return False

    extension_attempts = int(user_data.get("extension_attempts", 0) or 0)
    is_retry_prompt = is_invalid_extension_retry_prompt(user_text)
    if user_data.get("extension_digits_sent") and not is_retry_prompt:
        return False

    if extension_attempts >= 2:
        return False

    ext_clean = resolve_extension_digits(extension_val)
    if len(ext_clean) != 4:
        logger.info("Extension skipped: must resolve to 4 digits (got '%s')", ext_clean)
        return False

    twiml = build_extension_prompt_timing_twiml(ext_clean)
    user_data["extension_digits_sent"] = True
    user_data["extension_attempts"] = extension_attempts + 1
    user_data["extension_redirect_stream_sid"] = user_data.get("current_stream_sid")

    try:
        twilio_client = Client(account_sid, auth_token)
        await asyncio.to_thread(twilio_client.calls(call_sid).update, twiml=twiml)
        return True
    except Exception as ext_err:
        user_data["extension_digits_sent"] = False
        user_data.pop("extension_redirect_stream_sid", None)
        logger.error("Extension DTMF send failed: %s", ext_err)
        return False


# -------------------------------------------------------------------
# MAIN VOICE AGENT HANDLER
# -------------------------------------------------------------------
async def handle_voice_agent(
    websocket_client: WebSocket,
    stream_sid: str,
    call_sid: str,
    user_data: dict = None,
):
    """Configure and run the Pipecat real-time voice pipeline for one call."""
    if user_data is None:
        user_data = {}

    user_data["current_stream_sid"] = stream_sid

    logger.info("handle_voice_agent_started call_sid=%s stream_sid=%s", call_sid, stream_sid)

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
                params=TwilioFrameSerializer.InputParams(auto_hang_up=True),
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
        auto_detect_source_languages=["en-IN", "es-ES"],
    )

    tts = AzureTTSService(
        api_key=AZURE_SPEECH_API_KEY,
        region=AZURE_SPEECH_REGION,
        voice=TTS_VOICE,
        text_aggregation_mode=TextAggregationMode.SENTENCE,
        settings=AzureTTSService.Settings(style=VOICE_STYLE, rate=VOICE_RATE),
    )

    call_type = user_data.get("call_type")

    try:
        system_prompt = get_prompt_one(user_data) if call_type == 1 else get_prompt_two(user_data)
        fire_and_forget_log(user_data, "Call Script Generation", "callhandler_e002", True)
    except Exception as e:
        fire_and_forget_log(
            user_data,
            "Call Script Generation",
            "callhandler_e002",
            False,
            "AI call script generation failed due to configuration or model error",
        )
        logger.exception("call_script_generation_failed call_sid=%s: %s", call_sid, e)
        raise

    context = OpenAILLMContext([{"role": "system", "content": system_prompt}])

    smart_turn = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=0.5))
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
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=smart_turn)],
            ),
        ),
    )

    context_limiter = ContextLimiterProcessor(max_messages=MAX_CONTEXT_MESSAGES)
    record_id = user_data.get("call_id") or call_sid
    audiobuffer = AudioBufferProcessor(num_channels=NUM_CHANNELS, enable_turn_audio=False)

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

    turn_end_time = 0
    start_silence_time = 0
    current_language = "en"
    language_locked = False

    # STT/TTS monitoring (dict is mutable, so handlers need no nonlocal).
    speech_stats = {"stt_count": 0, "stt_conf": [], "tts_ttft": []}

    # ---------------------------------------------------------------
    # EVENT HANDLERS
    # ---------------------------------------------------------------
    @stt.event_handler("on_transcript")
    async def on_stt_transcript(service, transcription):
        nonlocal turn_end_time
        turn_end_time = time.time()
        speech_stats["stt_count"] += 1
        conf = transcription.get("confidence") if isinstance(transcription, dict) else getattr(transcription, "confidence", None)
        if isinstance(conf, (int, float)):
            speech_stats["stt_conf"].append(float(conf))

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        try:
            await blob_manager.upload_chunk(record_id, audio)
        except Exception:
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

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message: UserTurnStoppedMessage):
        nonlocal start_silence_time, current_language, language_locked, context

        start_silence_time = time.time()
        user_text = message.content or ""
        print(f"Conversation - User: {user_text}")

        # Capture any reference/check number the customer provides.
        capture_reference_number(user_data, user_text)

        if await enter_extension_after_prompt(call_sid, user_data, user_text):
            return

        idle_handler.reset()

        if language_locked:
            return

        detected_language = detect_language_from_text(user_text)
        if detected_language == current_language:
            return

        current_language = detected_language
        language_locked = True
        logger.info("Language locked from user speech: %s", current_language)

        try:
            new_system_prompt = get_prompt(user_data, language=current_language)
        except TypeError:
            new_system_prompt = system_prompt

        context.messages = [
            {"role": "system", "content": new_system_prompt},
            {"role": "user", "content": user_text},
        ]

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await idle_handler.handle()

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage):
        if message.content:
            assistant_latency = time.time() - turn_end_time
            speech_stats["tts_ttft"].append(round(assistant_latency, 4))
            print(
                f"Conversation - Assistant: {message.content} "
                f"| TTFT: {assistant_latency:.3f}s"
            )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("client_connected call_sid=%s stream_sid=%s", call_sid, stream_sid)
        await audiobuffer.start_recording()

        # If an extension is present, stay silent so the IVR greeting is captured
        # and DTMF digits can be sent at the right moment.
        has_extension = bool(user_data.get("extension") or user_data.get("phone_extension"))
        if not has_extension:
            await task.queue_frames([OpenAILLMContextFrame(context)])

        call_cutoff_manager.set_queue_frame_callback(task.queue_frames)
        await call_cutoff_manager.start_call_timer(call_sid, duration_seconds=MAX_CALL_DURATION)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client_disconnected call_sid=%s stream_sid=%s", call_sid, stream_sid)
        await audiobuffer.stop_recording()
        await task.cancel()

    # ---------------------------------------------------------------
    # RUN
    # ---------------------------------------------------------------
    call_success = True
    failure_msg = None

    try:
        runner = PipelineRunner(handle_sigint=False, force_gc=FORCE_GC_ON_RUN)
        await runner.run(task)
    except Exception as e:
        print(f"Error during call: {e}")
        call_success = False
        failure_msg = f"Server crashed or error during call: {str(e)}"
        logger.exception("voice_agent_call_failed call_sid=%s: %s", call_sid, e)

    # Extension redirect hand-off: a new stream will take over this call.
    if user_data.get("extension_redirect_stream_sid") == stream_sid:
        logger.info("extension_redirect_stream_handoff call_sid=%s stream_sid=%s", call_sid, stream_sid)
        return {
            "call_sid": call_sid,
            "stream_sid": stream_sid,
            "call_success": True,
            "failure_message": None,
            "telemetry_summary": None,
            "stream_handoff": True,
        }

    await call_cutoff_manager.cancel_timer(call_sid)

    if idle_handler.triggered:
        call_success = False
        failure_msg = "Call disconnected due to user inactivity (idle)."
    elif call_cutoff_manager.has_exceeded(call_sid):
        call_success = False
        failure_msg = f"Call exceeded maximum allowed duration of {MAX_CALL_DURATION} seconds."

    # ---------------------------------------------------------------
    # TRANSCRIPT (redacted)
    # ---------------------------------------------------------------
    raw_transcript = [
        msg
        for msg in context.get_messages()
        if msg.get("role") in ["user", "assistant"] and msg.get("content")
    ]
    full_transcript = [
        {**msg, "content": redact_sensitive_text(msg.get("content", ""))}
        for msg in raw_transcript
    ]

    # ---------------------------------------------------------------
    # FINALIZE RECORDING
    # ---------------------------------------------------------------
    try:
        recording_info = await blob_manager.finalize_recording(record_id, SAMPLE_RATE, NUM_CHANNELS)
        fire_and_forget_log(user_data, "Blob Audio Recording", "callhandler_e007", True)
    except Exception as e:
        fire_and_forget_log(
            user_data,
            "Blob Audio Recording",
            "callhandler_e007",
            False,
            "Audio recording failed during finalization",
        )
        logger.exception("blob_audio_finalization_failed call_sid=%s: %s", call_sid, e)
        recording_info = None

    # ---------------------------------------------------------------
    # REPORT CALL RESULT
    # ---------------------------------------------------------------
    try:
        notify_agent_orchestration_service(user_data.get("call_id", " "))
        logger.info(full_transcript)

        _conf = speech_stats["stt_conf"]
        _ttft = speech_stats["tts_ttft"]
        telemetry_summary = {
            "stt": {
                "transcript_count": speech_stats["stt_count"],
                "average_confidence": round(sum(_conf) / len(_conf), 4) if _conf else None,
            },
            "tts": {
                "turn_count": len(_ttft),
                "average_ttft_seconds": round(sum(_ttft) / len(_ttft), 4) if _ttft else None,
                "max_ttft_seconds": max(_ttft) if _ttft else None,
            },
        }

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
        logger.info("call_report_completed call_sid=%s call_success=%s", call_sid, call_success)
    except Exception as e:
        logger.exception("call_report_failed call_sid=%s: %s", call_sid, e)
        raise

    return {
        "call_sid": call_sid,
        "stream_sid": stream_sid,
        "call_success": call_success,
        "failure_message": failure_msg,
        "telemetry_summary": None,
    }
