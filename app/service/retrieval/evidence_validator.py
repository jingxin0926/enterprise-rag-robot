"""RAG 生成前的证据充分性判定器。"""

import json
from dataclasses import dataclass

from loguru import logger

from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.prompts.loader import get_prompt_loader


@dataclass(frozen=True)
class EvidenceVerdict:
    """证据判定结果。"""

    sufficient: bool
    reason: str


class EvidenceValidator:
    """基于原始问题和检索证据判断是否允许进入答案生成。"""

    def __init__(self) -> None:
        self._llm = get_deepseek_client()
        self._loader = get_prompt_loader()

    @staticmethod
    def parse_response(raw: str) -> EvidenceVerdict:
        """解析模型返回的 JSON，格式异常时按拒答处理。"""
        content = raw.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(content)
            return EvidenceVerdict(sufficient=bool(payload.get("sufficient", False)), reason=str(payload.get("reason", "")))
        except (IndexError, json.JSONDecodeError, TypeError):
            return EvidenceVerdict(sufficient=False, reason="证据判定结果格式异常")

    async def validate(self, question: str, context: str) -> EvidenceVerdict:
        """调用低 Token 判定请求，禁止依据通用词或常识放行。"""
        prompt = self._loader.load("evidence_validator", question=question, context=context[:2400])
        response = await self._llm.chat(
            [ChatMessage(role="user", content=prompt)],
            temperature=0,
            max_tokens=80,
        )
        verdict = self.parse_response(response.content)
        logger.info("[RAG-EvidenceValidator] sufficient={} reason={}", verdict.sufficient, verdict.reason[:80])
        return verdict
