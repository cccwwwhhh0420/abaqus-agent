# -*- coding: utf-8 -*-
"""OpenAI-compatible LLM client with streaming and tool call parsing."""

from typing import Any, Dict, Generator, List, Optional, Tuple
from openai import OpenAI
from abaqus_agent.config import API_KEY, BASE_URL, MODEL_NAME


class LLMClient:
    """Client for OpenAI-compatible Chat Completion API."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or API_KEY
        self.base_url = base_url or BASE_URL
        self.model = model or MODEL_NAME
        self.client = OpenAI(
            api_key=self.api_key if self.api_key else "EMPTY",
            base_url=self.base_url
        )

    def is_configured(self) -> bool:
        """Check if API key is present."""
        return bool(self.api_key and self.api_key != "EMPTY")

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Generator[Tuple[str, str], None, None], Dict[str, Any]]:
        """Stream chat completions.

        Returns (token_generator, result_container_dict).
        result_container_dict['message'] will be populated when token_generator finishes.
        """
        if not self.is_configured():
            raise ValueError("API Key is missing. Please configure OPENAI_API_KEY in .env file.")

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)
        result_container: Dict[str, Any] = {"message": None}

        def _generator() -> Generator[Tuple[str, str], None, None]:
            full_content: List[str] = []
            full_reasoning: List[str] = []
            tool_calls_dict: Dict[int, Dict[str, Any]] = {}

            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # Handle reasoning content (e.g. DeepSeek R1)
                reasoning_chunk = getattr(delta, "reasoning_content", None)
                if reasoning_chunk:
                    full_reasoning.append(reasoning_chunk)
                    yield ("reasoning", reasoning_chunk)

                # Handle standard content
                content_chunk = delta.content
                if content_chunk:
                    full_content.append(content_chunk)
                    yield ("content", content_chunk)

                # Handle tool calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_dict:
                            tool_calls_dict[idx] = {
                                "id": tc.id or f"call_{idx}",
                                "type": "function",
                                "function": {
                                    "name": tc.function.name or "",
                                    "arguments": tc.function.arguments or ""
                                }
                            }
                        else:
                            if tc.function.name:
                                tool_calls_dict[idx]["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls_dict[idx]["function"]["arguments"] += tc.function.arguments

            final_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": "".join(full_content) if full_content else None,
            }
            if tool_calls_dict:
                final_msg["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

            result_container["message"] = final_msg

        return _generator(), result_container
