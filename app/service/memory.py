"""
对话记忆管理服务（双层记忆架构）

设计思路：
┌─────────────────────────────────────────────┐
│  短期记忆（Short-term Memory）                │
│  - 保存最近 N 轮对话原文                       │
│  - 保持对话连贯性和上下文                      │
│  - 超过阈值后触发压缩 → 转入长期记忆            │
└─────────────────────────────────────────────┘
                    ↓ 压缩
┌─────────────────────────────────────────────┐
│  长期记忆（Long-term Memory）                 │
│  - 用 LLM 把旧对话压缩成摘要                   │
│  - 摘要拼在 system prompt 后面                 │
│  - 大幅减少 token 消耗，保留关键信息             │
└─────────────────────────────────────────────┘

优势：
- token 用量可控：不管聊多少轮，发给 LLM 的上下文始终有上限
- 关键信息不丢：压缩时保留了核心内容
- 成本优化：压缩一次约 200 token，比全量历史便宜得多
"""

import json
from datetime import datetime
from pathlib import Path
from loguru import logger

from app.core.config import settings
from app.infra.cache.redis_client import get_redis
from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.prompts.loader import get_prompt_loader


# 内存 fallback 存储
_memory_store: dict[str, dict] = {}


class ConversationMemory:
    """
    对话记忆管理器

    用法：
        memory = ConversationMemory()

        # 添加对话
        await memory.add_message(session_id, "user", "年假怎么请？")
        await memory.add_message(session_id, "assistant", "需要在OA系统提交...")

        # 获取用于发送给 LLM 的上下文（自动包含摘要+近期对话）
        context = await memory.get_context(session_id)
    """

    def __init__(
        self,
        max_short_term: int = 6,       # 短期记忆保留最近几轮（1轮 = user+assistant 2条）
        compress_threshold: int = 10,   # 超过多少条消息触发压缩
        session_ttl: int = 604800,     # 会话过期时间（秒），默认7天
    ) -> None:
        self._max_short_term = max_short_term * 2   # 轮次转消息条数
        self._compress_threshold = compress_threshold
        self._session_ttl = session_ttl
        self._llm = get_deepseek_client()
        self._loader = get_prompt_loader()

    # ------------------------------------------------------------------
    # 存储操作（Redis 优先，内存 fallback）
    # 短期记忆：Redis（可过期）
    # 长期记忆（摘要）：独立持久化存储（不跟随 Redis 过期）
    # ------------------------------------------------------------------
    async def _get_data(self, session_id: str) -> dict:
        """获取会话的完整记忆数据"""
        redis = await get_redis()
        if redis:
            key = f"memory:{session_id}"
            raw = await redis.get(key)
            if raw:
                data = json.loads(raw)
                # 如果 Redis 里没有摘要，尝试从持久化层加载
                if not data.get("summary"):
                    data["summary"] = await self._load_summary(session_id)
                return data
            else:
                # Redis 过期了，但长期记忆可能还在持久化层
                summary = await self._load_summary(session_id)
                return {
                    "summary": summary,
                    "messages": [],
                    "total_turns": 0,
                }
        else:
            if session_id in _memory_store:
                return _memory_store[session_id]

        # 全新会话
        return {
            "summary": "",
            "messages": [],
            "total_turns": 0,
        }

    async def _save_data(self, session_id: str, data: dict) -> None:
        """保存会话记忆数据"""
        redis = await get_redis()
        if redis:
            key = f"memory:{session_id}"
            await redis.set(key, json.dumps(data, ensure_ascii=False), ex=self._session_ttl)
        else:
            _memory_store[session_id] = data

        # 如果有摘要，额外持久化一份（不受 Redis TTL 影响）
        if data.get("summary"):
            await self._persist_summary(session_id, data["summary"])

    # ------------------------------------------------------------------
    # 长期记忆持久化（摘要独立存储，不过期）
    # 当前用本地文件实现，生产环境应替换为 MySQL/PostgreSQL
    # ------------------------------------------------------------------
    def _get_summary_path(self, session_id: str) -> Path:
        """获取摘要文件路径"""
        summary_dir = Path(__file__).resolve().parent.parent.parent / "data" / "memory"
        summary_dir.mkdir(parents=True, exist_ok=True)
        return summary_dir / f"{session_id}.json"

    async def _persist_summary(self, session_id: str, summary: str) -> None:
        """持久化保存摘要（不受 Redis 过期影响）"""
        path = self._get_summary_path(session_id)
        data = {"summary": summary, "updated_at": datetime.now().isoformat()}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    async def _load_summary(self, session_id: str) -> str:
        """从持久化层加载摘要"""
        path = self._get_summary_path(session_id)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("summary", "")
        return ""

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------
    async def add_message(self, session_id: str, role: str, content: str) -> None:
        """
        添加一条消息到记忆

        如果短期记忆超过阈值，自动触发压缩

        Args:
            session_id: 会话ID
            role: 角色（user / assistant）
            content: 消息内容
        """
        data = await self._get_data(session_id)
        data["messages"].append({"role": role, "content": content})

        if role == "assistant":
            data["total_turns"] += 1

        # 检查是否需要压缩
        if len(data["messages"]) >= self._compress_threshold:
            await self._compress(data)

        await self._save_data(session_id, data)

    async def get_context(self, session_id: str) -> list[dict]:
        """
        获取用于发送给 LLM 的上下文消息列表

        返回格式：[摘要提示(如有)] + [近期对话原文]
        这个列表拼在 system prompt 后面发给 LLM

        Args:
            session_id: 会话ID

        Returns:
            消息列表（role + content 格式）
        """
        data = await self._get_data(session_id)
        context = []

        # 如果有长期记忆摘要，作为 system 级别的补充上下文
        if data["summary"]:
            context.append({
                "role": "system",
                "content": f"[历史对话摘要]\n{data['summary']}",
            })

        # 加上短期记忆（最近几轮的原文）
        recent = data["messages"][-self._max_short_term:]
        context.extend(recent)

        return context

    async def get_stats(self, session_id: str) -> dict:
        """获取记忆统计信息"""
        data = await self._get_data(session_id)
        return {
            "session_id": session_id,
            "total_turns": data["total_turns"],                  # 历史总轮次
            "short_term_count": len(data["messages"]),           # 短期记忆条数
            "has_summary": bool(data["summary"]),                # 是否有长期记忆摘要
            "summary_preview": data["summary"][:100] if data["summary"] else "",  # 摘要预览
        }

    async def clear(self, session_id: str) -> None:
        """清空会话记忆"""
        redis = await get_redis()
        if redis:
            await redis.delete(f"memory:{session_id}")
        else:
            _memory_store.pop(session_id, None)
        logger.info("[Memory] 会话记忆已清空 | session={}", session_id[:8])

    # ------------------------------------------------------------------
    # 压缩逻辑
    # ------------------------------------------------------------------
    async def _compress(self, data: dict) -> None:
        """
        将旧对话压缩为摘要

        流程：
        1. 取出超出短期窗口的旧消息
        2. 用 LLM 生成摘要
        3. 摘要追加到长期记忆
        4. 短期记忆只保留最近 N 条
        """
        messages = data["messages"]

        # 需要压缩的部分：除了最近 max_short_term 条之外的旧消息
        to_compress = messages[:-self._max_short_term]
        to_keep = messages[-self._max_short_term:]

        if not to_compress:
            return

        # 格式化旧对话为文本
        history_text = "\n".join(
            [f"{m['role']}: {m['content']}" for m in to_compress]
        )

        # 如果已有摘要，把旧摘要也一起传入，让 LLM 做增量压缩
        if data["summary"]:
            history_text = f"[已有摘要]\n{data['summary']}\n\n[新增对话]\n{history_text}"

        try:
            # 调用 LLM 压缩
            prompt = self._loader.load("memory_summarize", history=history_text)
            response = await self._llm.chat(
                [ChatMessage(role="user", content=prompt)],
                max_tokens=300,
                temperature=0.2,
            )

            new_summary = response.content.strip()
            data["summary"] = new_summary
            data["messages"] = to_keep

            # 压缩后立刻持久化摘要（确保 Redis 过期也不丢）
            logger.info(
                "[Memory] 对话压缩完成 | 压缩{}条 → 摘要{}字 | 保留近期{}条",
                len(to_compress),
                len(new_summary),
                len(to_keep),
            )

        except Exception as e:
            logger.warning("[Memory] 压缩失败，保留原始消息 | error={}", e)
            # 压缩失败时降级：简单截断，避免无限增长
            data["messages"] = to_keep
