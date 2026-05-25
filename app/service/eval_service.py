"""
RAG 评测服务（LLM-as-Judge）

核心思路：
用 LLM 对 RAG 系统的输出质量进行自动化评估，量化三个维度：
1. Faithfulness（忠实度）：回答是否基于参考资料，有无幻觉
2. Relevancy（相关性）：回答是否切中用户问题
3. Context Precision（上下文精确度）：检索出来的资料是否与问题相关

为什么用 LLM 当裁判：
- 人工评测成本太高，无法持续进行
- LLM 评分与人类打分相关性达 0.8+（业界研究验证）
- 支持自动化批量跑，可集成到 CI/CD 管道

生产用途：
- 上线前跑评测集，确保 RAG 质量达标
- Prompt 调整后回归测试，防止效果退化
- 监控线上回答质量趋势
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.prompts.loader import get_prompt_loader


@dataclass
class EvalScore:
    """单项评测分数"""

    metric: str          # 指标名称
    score: int           # 1-5 分
    reason: str          # 打分理由
    error: str = ""      # 如果评测出错，记录原因


@dataclass
class EvalResult:
    """完整评测结果"""

    question: str
    context: str
    answer: str
    scores: list[EvalScore] = field(default_factory=list)
    avg_score: float = 0.0
    evaluated_at: str = ""

    def __post_init__(self):
        self.evaluated_at = datetime.now().isoformat()

    def compute_avg(self) -> None:
        """计算平均分"""
        valid_scores = [s.score for s in self.scores if s.score > 0]
        self.avg_score = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else 0.0


@dataclass
class BatchEvalSummary:
    """批量评测汇总"""

    total: int = 0
    avg_faithfulness: float = 0.0
    avg_relevancy: float = 0.0
    avg_context_precision: float = 0.0
    overall_avg: float = 0.0
    results: list[EvalResult] = field(default_factory=list)
    evaluated_at: str = ""

    def __post_init__(self):
        self.evaluated_at = datetime.now().isoformat()


class EvalService:
    """
    RAG 评测服务

    支持单条评测和批量评测，输出结构化打分结果。

    用法：
        eval_svc = EvalService()
        result = await eval_svc.evaluate(question, context, answer)
        print(f"平均分: {result.avg_score}")
    """

    def __init__(self) -> None:
        self._llm = get_deepseek_client()
        self._loader = get_prompt_loader()

    async def _judge(self, prompt_name: str, **kwargs: str) -> EvalScore:
        """
        调用 LLM 对某个维度进行打分

        Args:
            prompt_name: 评测 Prompt 文件名（如 eval_faithfulness）
            **kwargs: Prompt 模板中的变量

        Returns:
            EvalScore 打分结果
        """
        metric = prompt_name.replace("eval_", "")

        try:
            # 加载评测 Prompt 并注入变量
            prompt_content = self._loader.load(prompt_name, **kwargs)

            messages = [
                ChatMessage(role="user", content=prompt_content),
            ]

            # 调用 LLM（低温度确保评测稳定）
            response = await self._llm.chat(
                messages,
                max_tokens=200,
                temperature=0.1,
            )

            # 解析 JSON 结果
            raw = response.content.strip()
            # 处理可能包含 markdown 代码块的情况
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(raw)
            score = int(result.get("score", 0))
            reason = result.get("reason", "")

            # 校验分数范围
            if not 1 <= score <= 5:
                score = max(1, min(5, score))

            logger.info("[Eval] {} = {}/5 | {}", metric, score, reason[:50])
            return EvalScore(metric=metric, score=score, reason=reason)

        except json.JSONDecodeError as e:
            logger.warning("[Eval] {} JSON解析失败: {}", metric, e)
            return EvalScore(metric=metric, score=0, reason="", error=f"JSON解析失败: {e}")
        except Exception as e:
            logger.error("[Eval] {} 评测异常: {}", metric, e)
            return EvalScore(metric=metric, score=0, reason="", error=str(e))

    async def evaluate(self, question: str, context: str, answer: str) -> EvalResult:
        """
        对单条 RAG 输出进行完整评测

        Args:
            question: 用户问题
            context: 检索到的参考资料
            answer: RAG 系统生成的回答

        Returns:
            EvalResult 包含三个维度的评分
        """
        result = EvalResult(question=question, context=context, answer=answer)

        # 三个维度并行评测（后续可改为 asyncio.gather 并行）
        # 1. 忠实度
        faithfulness = await self._judge(
            "eval_faithfulness",
            question=question,
            context=context,
            answer=answer,
        )
        result.scores.append(faithfulness)

        # 2. 相关性
        relevancy = await self._judge(
            "eval_relevancy",
            question=question,
            answer=answer,
        )
        result.scores.append(relevancy)

        # 3. 上下文精确度
        context_precision = await self._judge(
            "eval_context_precision",
            question=question,
            context=context,
        )
        result.scores.append(context_precision)

        # 计算平均分
        result.compute_avg()

        logger.info(
            "[Eval] 评测完成 | question='{}...' avg_score={}",
            question[:20],
            result.avg_score,
        )
        return result

    async def batch_evaluate(self, test_cases: list[dict]) -> BatchEvalSummary:
        """
        批量评测

        Args:
            test_cases: 测试用例列表，每项包含 question/context/answer

        Returns:
            BatchEvalSummary 汇总结果
        """
        summary = BatchEvalSummary(total=len(test_cases))
        faithfulness_scores = []
        relevancy_scores = []
        context_precision_scores = []

        for i, case in enumerate(test_cases, 1):
            logger.info("[Eval] 批量评测 {}/{}", i, len(test_cases))
            result = await self.evaluate(
                question=case["question"],
                context=case["context"],
                answer=case["answer"],
            )
            summary.results.append(result)

            # 按维度收集分数
            for score in result.scores:
                if score.score > 0:  # 排除评测失败的
                    if score.metric == "faithfulness":
                        faithfulness_scores.append(score.score)
                    elif score.metric == "relevancy":
                        relevancy_scores.append(score.score)
                    elif score.metric == "context_precision":
                        context_precision_scores.append(score.score)

        # 计算各维度平均分
        if faithfulness_scores:
            summary.avg_faithfulness = round(sum(faithfulness_scores) / len(faithfulness_scores), 2)
        if relevancy_scores:
            summary.avg_relevancy = round(sum(relevancy_scores) / len(relevancy_scores), 2)
        if context_precision_scores:
            summary.avg_context_precision = round(
                sum(context_precision_scores) / len(context_precision_scores), 2
            )

        # 总体平均
        all_scores = faithfulness_scores + relevancy_scores + context_precision_scores
        summary.overall_avg = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

        logger.info(
            "[Eval] 批量评测完成 | total={} faithfulness={} relevancy={} precision={} overall={}",
            summary.total,
            summary.avg_faithfulness,
            summary.avg_relevancy,
            summary.avg_context_precision,
            summary.overall_avg,
        )
        return summary
