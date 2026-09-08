import os 

# ---------------------------------------------------------
# AUDIO ENGINE PARAMETERS
# ---------------------------------------------------------
# Twilio and standard phone lines use 8000Hz Mono audio.
SAMPLE_RATE = 8000
NUM_CHANNELS = 1

# ---------------------------------------------------------
# VOICE ACTIVITY DETECTION (VAD)
# ---------------------------------------------------------
# VAD_STOP_DURATION: How many seconds of silence before the AI 
# thinks you are done talking. 0.5s provides a more natural rhythm.
VAD_STOP_DURATION = 0.5      

# VAD_MIN_SPEECH_DURATION: Minimum audio length to be called "speech."
# 0.4s helps prevent triggers from background noise or breathing.
VAD_MIN_SPEECH_DURATION = 0.4 

# ---------------------------------------------------------
# CONVERSATION BEHAVIOR
# ---------------------------------------------------------
# ALLOW_INTERRUPTIONS: If True, the AI will stop talking the 
# instant it hears the user speak (Barge-in).
ALLOW_INTERRUPTIONS = True

# FORCE_GC_ON_RUN: Cleans up RAM after every call. 
# Keeps the server stable for long-term use.
FORCE_GC_ON_RUN = True  

# ---------------------------------------------------------
# LLM & BRAIN MEMORY
# ---------------------------------------------------------
# MAX_CONTEXT_MESSAGES: How many turns of history the AI remembers.
# 20 turns ensures the AI remembers the entire conversation for verification and closing.
MAX_CONTEXT_MESSAGES = 20

# Azure Model configuration
AZURE_DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT", "gpt-4.1-nano")
LLM_MODEL_VERSION = os.getenv("AZURE_API_VERSION", "2024-05-01-preview")

# ---------------------------------------------------------
# AI VOICE & LANGUAGE (TTS/STT)
# ---------------------------------------------------------
# TTS_VOICE: The specific AI voice character (Ava is professional).
TTS_VOICE = "en-US-AvaNeural"

# VOICE_STYLE: The emotional tone (professional, empathetic, etc.).
VOICE_STYLE = "professional" 

# VOICE_RATE: Speech speed. 1.05 is slightly faster than human,
# which actually sounds more "alert" and professional over the phone.
VOICE_RATE = "1.05"         

# STT_LANGUAGE: The language the AI expects to hear.
STT_LANGUAGE = "en-US"

# ---------------------------------------------------------
# TELEPHONY (TWILIO)
# ---------------------------------------------------------
# The specific events we want Twilio to report to our status-callback.
TWILIO_CALLBACK_EVENTS = ['completed', 'failed', 'busy', 'no-answer']

# --- EXAMPLE: HOW TO ADD A NEW FEATURE PARAMETER ---
# ENABLE_SENTIMENT_ANALYSIS = True  # Toggle for analyzing customer mood
MAX_CALL_DURATION=60*5