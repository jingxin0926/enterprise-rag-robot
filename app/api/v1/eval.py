"""
RAG 评测接口

功能：
1. POST /api/v1/eval/single   — 对单条 RAG 输出进行质量评测
2. POST /api/v1/eval/rag      — 端到端评测（输入问题 → 自动检索+生成+评分）
"""

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from app.core.response import R
from app.service.eval_service import EvalService
from app.service.rag_service import RAGService

router = APIRouter(prefix="/eval", tags=["评测"])

# 服务实例
_eval_service = EvalService()
_rag_service = RAGService()


class SingleEvalRequest(BaseModel):
    """单条评测请求（手动提供 context 和 answer）"""

    question: str = Field(..., description="用户问题")
    context: str = Field(..., description="检索到的参考资料")
    answer: str = Field(..., description="RAG 系统的回答")


class RAGEvalRequest(BaseModel):
    """端到端 RAG 评测请求（只需提供问题，系统自动检索+生成+评分）"""

    questions: list[str] = Field(..., description="待评测的问题列表", min_length=1, max_length=20)


@router.post("/single", summary="单条 RAG 质量评测")
async def eval_single(req: SingleEvalRequest):
    """
    对一条 RAG 输出进行三维度评分：
    - Faithfulness（忠实度）：回答是否基于参考资料
    - Relevancy（相关性）：回答是否切中问题
    - Context Precision（上下文精确度）：检索结果是否相关
    """
    result = await _eval_service.evaluate(
        question=req.question,
        context=req.context,
        answer=req.answer,
    )
    return R.ok(data={
        "question": result.question,
        "avg_score": result.avg_score,
        "scores": [
            {"metric": s.metric, "score": s.score, "reason": s.reason}
            for s in result.scores
        ],
        "evaluated_at": result.evaluated_at,
    })


@router.post("/rag", summary="端到端 RAG 评测")
async def eval_rag(req: RAGEvalRequest):
    """
    端到端评测流程：
    1. 对每个问题执行完整 RAG 流程（检索 + 生成）
    2. 用 LLM-as-Judge 对结果自动打分
    3. 返回各维度平均分和逐条明细
    """
    test_cases = []

    for question in req.questions:
        # 执行 RAG 流程
        rag_result = await _rag_service.query(question)
        # 组装检索到的上下文
        context = "\n".join(
            [f"[{s.get('source', '未知')}] (score: {s.get('score', 0)})" for s in rag_result.sources]
        )
        test_cases.append({
            "question": question,
            "context": context if context else "（未检索到相关内容）",
            "answer": rag_result.answer,
        })

    # 批量评测
    summary = await _eval_service.batch_evaluate(test_cases)

    return R.ok(data={
        "total": summary.total,
        "avg_faithfulness": summary.avg_faithfulness,
        "avg_relevancy": summary.avg_relevancy,
        "avg_context_precision": summary.avg_context_precision,
        "overall_avg": summary.overall_avg,
        "results": [
            {
                "question": r.question,
                "answer": r.answer[:200],
                "avg_score": r.avg_score,
                "scores": [
                    {"metric": s.metric, "score": s.score, "reason": s.reason}
                    for s in r.scores
                ],
            }
            for r in summary.results
        ],
        "evaluated_at": summary.evaluated_at,
    })
