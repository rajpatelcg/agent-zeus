# """
# app/caller_features/call_end.py
# ================================
# Reusable maximum call duration enforcer for the Pipecat voice pipeline.

# Manages call cutoff functionality to limit calls to a specified duration.
# """

# import asyncio
# from typing import Callable
# from twilio.rest import Client
# from loguru import logger
# from pipecat.frames.frames import TextFrame, EndFrame
# from app.config.src import account_sid, auth_token

# # Message spoken when the duration limit is reached
# _DURATION_EXCEEDED_MESSAGE = (
#     "The call is going beyond the allocated duration. Our executive will reach out to you shortly. Thank you."
# )


# class CallCutoffManager:
#     """
#     Manages call cutoff functionality to limit calls to a specified duration.
#     """
    
#     def __init__(self):
#         self.active_timers = {}  # {call_sid: asyncio.Task}
#         self.queue_frame_callback = None  # Temporary/latest callback
#         self.queue_frame_callbacks = {}  # {call_sid: Callable}
#         self.exceeded_calls = set()  # {call_sid}
#         if account_sid and auth_token:
#             try:
#                 self.twilio_client = Client(account_sid, auth_token)
#             except Exception as e:
#                 logger.error(f"[CallCutoff] Error initializing Twilio client: {e}")
#                 self.twilio_client = None
#         else:
#             self.twilio_client = None
    
#     def set_queue_frame_callback(self, callback: Callable):
#         """Set the callback to queue frames to the pipeline."""
#         self.queue_frame_callback = callback
    
#     async def start_call_timer(self, call_sid: str, duration_seconds: int = 60):
#         """
#         Starts a timer for a call. When the duration expires, the call is automatically disconnected.
        
#         Args:
#             call_sid (str): Twilio Call SID
#             duration_seconds (int): Call duration limit in seconds
#         """
#         try:
#             # Map the latest queue callback specifically to this call_sid
#             if self.queue_frame_callback:
#                 self.queue_frame_callbacks[call_sid] = self.queue_frame_callback
            
#             # Create and store the timer task
#             task = asyncio.create_task(
#                 self._wait_and_cutoff(call_sid, duration_seconds)
#             )
#             self.active_timers[call_sid] = task
#             logger.info(f"[CallCutoff] Timer started for {call_sid} - will cutoff in {duration_seconds} seconds")
#         except Exception as e:
#             logger.error(f"[CallCutoff] Error starting timer for {call_sid}: {e}")
    
#     async def _wait_and_cutoff(self, call_sid: str, duration_seconds: int):
#         """
#         Internal function that waits for the duration and then cuts off the call.
#         """
#         try:
#             # Wait for the specified duration
#             await asyncio.sleep(duration_seconds)
            
#             # Cutoff the call after duration expires
#             await self.cutoff_call(call_sid)
#         except asyncio.CancelledError:
#             logger.info(f"[CallCutoff] Timer cancelled for {call_sid}")
#         except Exception as e:
#             logger.error(f"[CallCutoff] Error during cutoff for {call_sid}: {e}")
#         finally:
#             # Clean up the timer
#             self.active_timers.pop(call_sid, None)
    
#     async def cutoff_call(self, call_sid: str):
#         """
#         Sends a message to the user and then disconnects the call via Twilio API.
        
#         Args:
#             call_sid (str): Twilio Call SID to disconnect
#         """
#         try:
#             # Send message before cutting the call
#             callback = self.queue_frame_callbacks.get(call_sid)
#             if callback:
#                 cutoff_message = TextFrame(_DURATION_EXCEEDED_MESSAGE)
#                 await callback([cutoff_message])
#                 logger.warning(f"[CallCutoff] Cutoff message sent to {call_sid}")
                
#                 # Wait for TTS to start processing. wil have some time to tell the complete message..
#                 await asyncio.sleep(3)
                
#                 # Queue EndFrame for graceful shutdown
#                 await callback([EndFrame()])
#                 logger.warning(f"[CallCutoff] EndFrame queued for {call_sid}")
            
#             # Record that this call sid exceeded the limit
#             self.exceeded_calls.add(call_sid)

#             # Also disconnect via Twilio API as backup
#             if self.twilio_client:
#                 # Use loop.run_in_executor to avoid blocking the event loop on Twilio API call
#                 loop = asyncio.get_running_loop()
#                 await loop.run_in_executor(
#                     None,
#                     lambda: self.twilio_client.calls(call_sid).update(status='completed')
#                 )
#                 logger.warning(f"[CallCutoff] Call {call_sid} disconnected via Twilio API")
#         except Exception as e:
#             logger.error(f"[CallCutoff] Error during cutoff for {call_sid}: {e}")
    
#     async def cancel_timer(self, call_sid: str):
#         """
#         Manually cancels a call timer if it's still active.
        
#         Args:
#             call_sid (str): Twilio Call SID
#         """
#         if call_sid in self.active_timers:
#             task = self.active_timers[call_sid]
#             task.cancel()
#             try:
#                 await task
#             except asyncio.CancelledError:
#                 pass
#             self.active_timers.pop(call_sid, None)
#             logger.info(f"[CallCutoff] Timer cancelled for {call_sid}")
            
#     def has_exceeded(self, call_sid: str) -> bool:
#         """Returns True if the call SID was cut off due to duration limit."""
#         return call_sid in self.exceeded_calls
        
#     def cleanup_call(self, call_sid: str):
#         """Clean up tracking data for a call SID when it is finished."""
#         self.active_timers.pop(call_sid, None)
#         self.queue_frame_callbacks.pop(call_sid, None)
#         self.exceeded_calls.discard(call_sid)


# # Global instance
# call_cutoff_manager = CallCutoffManager()



# Adding the second itteration

"""
app/caller_features/call_end.py
================================
Reusable maximum call duration enforcer for the Pipecat voice pipeline.

Manages call cutoff functionality to limit calls to a specified duration.
"""

import asyncio
from typing import Callable
from twilio.rest import Client
from loguru import logger
from pipecat.frames.frames import TextFrame, EndFrame
from app.config.src import account_sid, auth_token

# Message spoken when the duration limit is reached
_DURATION_EXCEEDED_MESSAGE = (
    "The call is going beyond the allocated duration. Our executive will reach out to you shortly. Thank you."
)


class CallCutoffManager:
    """
    Manages call cutoff functionality to limit calls to a specified duration.
    """
    
    def __init__(self):
        self.active_timers = {}  # {call_sid: asyncio.Task}
        self.queue_frame_callback = None  # Temporary/latest callback
        self.queue_frame_callbacks = {}  # {call_sid: Callable}
        self.exceeded_calls = set()  # {call_sid}
        if account_sid and auth_token:
            try:
                self.twilio_client = Client(account_sid, auth_token)
            except Exception as e:
                logger.error(f"[CallCutoff] Error initializing Twilio client: {e}")
                self.twilio_client = None
        else:
            self.twilio_client = None
    
    def set_queue_frame_callback(self, callback: Callable):
        """Set the callback to queue frames to the pipeline."""
        self.queue_frame_callback = callback
    
    async def start_call_timer(self, call_sid: str, duration_seconds: int = 60):
        """
        Starts a timer for a call. When the duration expires, the call is automatically disconnected.
        
        Args:
            call_sid (str): Twilio Call SID
            duration_seconds (int): Call duration limit in seconds
        """
        try:
            # Map the latest queue callback specifically to this call_sid
            if self.queue_frame_callback:
                self.queue_frame_callbacks[call_sid] = self.queue_frame_callback
            
            # Create and store the timer task
            task = asyncio.create_task(
                self._wait_and_cutoff(call_sid, duration_seconds)
            )
            self.active_timers[call_sid] = task
            logger.info(f"[CallCutoff] Timer started for {call_sid} - will cutoff in {duration_seconds} seconds")
        except Exception as e:
            logger.error(f"[CallCutoff] Error starting timer for {call_sid}: {e}")
    
    async def _wait_and_cutoff(self, call_sid: str, duration_seconds: int):
        """
        Internal function that waits for the duration and then cuts off the call.
        """
        try:
            # Wait for the specified duration
            await asyncio.sleep(duration_seconds)
            
            # Cutoff the call after duration expires
            await self.cutoff_call(call_sid)
        except asyncio.CancelledError:
            logger.info(f"[CallCutoff] Timer cancelled for {call_sid}")
        except Exception as e:
            logger.error(f"[CallCutoff] Error during cutoff for {call_sid}: {e}")
        finally:
            # Clean up the timer
            self.active_timers.pop(call_sid, None)
    
    async def cutoff_call(self, call_sid: str):
        """
        Sends a message to the user and then disconnects the call via Twilio API.
        
        Args:
            call_sid (str): Twilio Call SID to disconnect
        """
        try:
            # Send message before cutting the call
            callback = self.queue_frame_callbacks.get(call_sid)
            if callback:
                cutoff_message = TextFrame(_DURATION_EXCEEDED_MESSAGE)
                await callback([cutoff_message, EndFrame()])
                logger.warning(f"[CallCutoff] Cutoff message and EndFrame queued for {call_sid}")
            
            # Record that this call sid exceeded the limit
            self.exceeded_calls.add(call_sid)

            # Also disconnect via Twilio API as backup (delayed to let the TTS finish speaking)
            if self.twilio_client:
                async def delayed_twilio_disconnect():
                    await asyncio.sleep(8)
                    try:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(
                            None,
                            lambda: self.twilio_client.calls(call_sid).update(status='completed')
                        )
                        logger.warning(f"[CallCutoff] Call {call_sid} disconnected via Twilio API backup")
                    except Exception as e:
                        logger.warning(f"[CallCutoff] Delayed Twilio backup disconnect failed (call might already be closed): {e}")
                
                asyncio.create_task(delayed_twilio_disconnect())
                logger.info(f"[CallCutoff] Scheduled delayed backup Twilio API disconnect task for {call_sid}")
        except Exception as e:
            logger.error(f"[CallCutoff] Error during cutoff for {call_sid}: {e}")
    
    async def cancel_timer(self, call_sid: str):
        """
        Manually cancels a call timer if it's still active.
        
        Args:
            call_sid (str): Twilio Call SID
        """
        if call_sid in self.active_timers:
            task = self.active_timers[call_sid]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.active_timers.pop(call_sid, None)
            logger.info(f"[CallCutoff] Timer cancelled for {call_sid}")
            
    def has_exceeded(self, call_sid: str) -> bool:
        """Returns True if the call SID was cut off due to duration limit."""
        return call_sid in self.exceeded_calls
        
    def cleanup_call(self, call_sid: str):
        """Clean up tracking data for a call SID when it is finished."""
        self.active_timers.pop(call_sid, None)
        self.queue_frame_callbacks.pop(call_sid, None)
        self.exceeded_calls.discard(call_sid)


# Global instance
call_cutoff_manager = CallCutoffManager()