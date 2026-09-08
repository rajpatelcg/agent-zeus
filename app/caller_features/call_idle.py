"""

Manages two-level user inactivity escalation:
  - Level 1 (first 15s silence): LISA politely checks if the user is still there.
  - Level 2 (another 15s silence): LISA says goodbye and the call ends.

Usage:
    from app.caller_features.call_idle import CallIdleHandler

    idle_handler = CallIdleHandler(context=context, task=task)

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        idle_handler.reset()

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        idle_handler.reset()

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await idle_handler.handle()
"""

from loguru import logger
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext, OpenAILLMContextFrame
from pipecat.frames.frames import TextFrame, EndFrame


# Message LISA speaks Level 1 (15seconds)
_IDLE_CHECKIN_MESSAGE = (
    "The user has been quiet. Politely ask if they are still there "
    "and whether they would like to continue."
)

# Message LISA speaks Level 2 (after 15 seconds)
_IDLE_GOODBYE_MESSAGE = (
    "It seems you may be busy right now. "
    "I'll go ahead and end the call. "
    "Please don't hesitate to call us back at your convenience. Have a great day!"
)


class CallIdleHandler:

    def __init__(self, context: OpenAILLMContext, task):
        """
        Args:
            context: The shared OpenAILLMContext for the current call.
                     Used to inject idle check-in system messages.
            task:    The PipelineTask. Used to queue frames (LLM triggers, EndFrame).
        """
        self._context  = context
        self._task     = task
        self._count    = 0
        self.triggered = False

    def reset(self):
        """
        Call this whenever the user speaks (turn started OR turn stopped).
        Resets the idle counter so escalation starts fresh.
        """
        if self._count > 0:
            logger.info(f"[Idle] User activity detected — resetting idle count (was {self._count}).")
        self._count = 0

    async def handle(self):
        """
        Called by the pipeline's on_user_turn_idle event.
        Escalates idle behaviour in two steps:
          - Step 1: Inject a check-in prompt and trigger a new LLM turn.
          - Step 2: Queue a farewell TextFrame + EndFrame to terminate the call.
        """
        self._count += 1
        logger.warning(f"[Idle] User idle timeout triggered. Level: {self._count}")

        if self._count == 1:
            # Level 1 — gentle check-in via a temporary system message
            logger.info("[Idle] Level 1 — injecting check-in prompt.")
            self._context.add_message({
                "role":    "system",
                "content": _IDLE_CHECKIN_MESSAGE,
            })
            await self._task.queue_frames([OpenAILLMContextFrame(self._context)])

        else:
            # Level 2 — user still not responding, terminate politely
            logger.error("[Idle] Level 2 — user unresponsive. Terminating call.")
            self.triggered = True
            await self._task.queue_frames([
                TextFrame(_IDLE_GOODBYE_MESSAGE),
                EndFrame(),
            ])

