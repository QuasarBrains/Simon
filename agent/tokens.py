from llms.llm import LLM, LLMError, Message, validate_history
from tools.index import Tool


class TokenControl:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    def trailing_window(
        self, messages: list[Message], tools: list[Tool]
    ) -> list[Message]:
        generation = self.llm.provider.settings.generation
        limits = []
        if generation.max_estimated_input_tokens is not None:
            limits.append(generation.max_estimated_input_tokens)
        output_limit = generation.reserved_output_tokens
        if generation.context_window_tokens is not None:
            limits.append(generation.context_window_tokens - output_limit)
        if not limits:
            return messages
        budget = min(limits)
        if budget <= 0:
            raise LLMError(
                "context_window_tokens leaves no input space after reserving "
                "the configured output and retry allowance."
            )

        validate_history(messages)
        prefix_end = 0
        while prefix_end < len(messages) and messages[prefix_end].role == "system":
            prefix_end += 1
        prefix = messages[:prefix_end]
        starts = [
            index
            for index in range(prefix_end, len(messages))
            if messages[index].role == "user"
        ]
        candidates = [prefix_end, *[index for index in starts if index > prefix_end]]
        for start in candidates:
            window = prefix + messages[start:]
            estimate = self.llm.estimate_input_tokens(window, tools, output_limit)
            if estimate <= budget:
                return window
        raise LLMError(
            f"The system prompt, tools, and newest iteration exceed the estimated "
            f"input budget ({budget} tokens). Older history cannot be removed further; "
            "reduce the current input/tool payload or increase the configured boundary. "
            "No compaction or partial-message truncation was performed."
        )
