"""对应 Java client/LlmClient.java + client/impl/OpenAiCompatibleLlmClient.java。

同一个 OpenAI-Compatible 端点（默认 DashScope compatible-mode v1 + qwen-plus），
base_url / model / api-key 全部来自环境变量；api-key 只能来自环境变量，不进代码。
"""

import logging
from abc import ABC, abstractmethod

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..config.settings import LlmSettings

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 40


class LlmClient(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        ...


class OpenAiCompatibleLlmClient(LlmClient):

    def __init__(self, llm: LlmSettings):
        self._llm = llm
        self._chat_model: ChatOpenAI | None = None

    def is_available(self) -> bool:
        return (
            self._llm.enabled
            and bool(self._llm.resolved_api_key().strip())
            and bool(self._llm.base_url and self._llm.base_url.strip())
            and bool(self._llm.model and self._llm.model.strip())
        )

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        try:
            message = self.get_chat_model().invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        except Exception as exception:  # noqa: BLE001 - 对齐 Java：任何失败都抛给调用方走规则兜底
            status_code = getattr(exception, "status_code", None)
            if status_code is not None and int(status_code) >= 400:
                raise RuntimeError(f"LLM 请求失败: HTTP {status_code} -> {exception}") from exception
            raise RuntimeError(f"LLM 请求失败: {exception}") from exception

        content = self._extract_content(message.content)
        if content is None or not content.strip():
            raise RuntimeError("LLM 未返回有效内容")
        return self._strip_markdown_code_fence(content)

    def get_chat_model(self) -> ChatOpenAI:
        if self._chat_model is None:
            self._chat_model = ChatOpenAI(
                model=self._llm.model,
                api_key=self._llm.resolved_api_key(),
                base_url=self._resolve_base_url(self._llm.base_url),
                temperature=self._llm.temperature,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                max_retries=0,
            )
        return self._chat_model

    def _resolve_base_url(self, base_url: str) -> str:
        # Java: baseUrl 去尾斜杠后以 /v1 结尾则请求 /chat/completions，否则请求 /v1/chat/completions。
        normalized = base_url[:-1] if base_url.endswith("/") else base_url
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        return normalized

    def _extract_content(self, content: object) -> str | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            builder = ""
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    builder += str(item.get("text", ""))
            return builder
        return str(content)

    def _strip_markdown_code_fence(self, text: str) -> str:
        trimmed = (text or "").strip()
        if trimmed.startswith("```"):
            first_line_end = trimmed.find("\n")
            last_fence = trimmed.rfind("```")
            if first_line_end >= 0 and last_fence > first_line_end:
                return trimmed[first_line_end + 1:last_fence].strip()
        return trimmed
