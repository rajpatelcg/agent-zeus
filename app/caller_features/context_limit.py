from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContextFrame, OpenAILLMContext
from app.caller_features.parameter import MAX_CONTEXT_MESSAGES

class ContextLimiterProcessor(FrameProcessor):
    """
    Limits the number of chat messages in the LLM context to prevent token overflow.
    Always preserves the 'system' role messages.
    """
    def __init__(self, max_messages: int = MAX_CONTEXT_MESSAGES):
        super().__init__()
        self.max_messages = max_messages

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, OpenAILLMContextFrame):
            system_msgs = [m for m in frame.context.messages if m.get("role") == "system"]
            chat_msgs = [m for m in frame.context.messages if m.get("role") != "system"]
            if len(chat_msgs) > self.max_messages:
                temp_context = OpenAILLMContext(system_msgs + chat_msgs[-self.max_messages:])
                await self.push_frame(OpenAILLMContextFrame(temp_context), direction)
                return
        await self.push_frame(frame, direction)