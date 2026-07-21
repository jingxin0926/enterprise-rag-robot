"""
RAG 评测接口

功能：
1. POST /api/v1/eval/single   — 对单条 RAG 输出进行质量评测
2. POST /api/v1/eval/rag      — 端到端评测（输入问题 → 自动检索+生成+评分）
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.response import R
from app.core.security import TokenPayload
from app.service.eval_service import EvalService
from app.service.evaluation_history_service import EvaluationHistoryService
from app.service.rag_service import RAGService

router = APIRouter(prefix="/eval", tags=["评测"])

# 服务实例
_eval_service = EvalService()


class SingleEvalRequest(BaseModel):
    """单条评测请求（手动提供 context 和 answer）"""

    question: str = Field(..., description="用户问题")
    context: str = Field(..., description="检索到的参考资料")
    answer: str = Field(..., description="RAG 系统的回答")


class RAGEvalRequest(BaseModel):
    """端到端 RAG 评测请求（只需提供问题，系统自动检索+生成+评分）"""

    questions: list[str] = Field(..., description="待评测的问题列表", min_length=1, max_length=20)


class DatasetEvalRequest(BaseModel):
    """版本化题库评测请求。"""

    limit: int | None = Field(default=None, ge=1, le=50, description="最多执行多少条题库用例")


def require_admin(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    """限制消耗模型配额的评测接口仅供管理员执行。"""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行评测任务")
    return user


@router.post("/single", summary="单条 RAG 质量评测")
async def eval_single(req: SingleEvalRequest, user: TokenPayload = Depends(require_admin)):
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
    return R.success(data={
        "question": result.question,
        "context": result.context,
        "answer": result.answer,
        "avg_score": result.avg_score,
        "scores": [
            {"metric": s.metric, "score": s.score, "reason": s.reason}
            for s in result.scores
        ],
        "evaluated_at": result.evaluated_at,
    })


@router.post("/rag", summary="端到端 RAG 评测")
async def eval_rag(req: RAGEvalRequest, user: TokenPayload = Depends(require_admin)):
    """
    端到端评测流程：
    1. 对每个问题执行完整 RAG 流程（检索 + 生成）
    2. 用 LLM-as-Judge 对结果自动打分
    3. 返回各维度平均分和逐条明细
    """
    test_cases = []
    # 在请求上下文内创建，确保检索器绑定当前 JWT 对应的租户 collection。
    rag_service = RAGService(use_semantic_cache=False)

    for question in req.questions:
        # 执行 RAG 流程
        rag_result = await rag_service.query(question)
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

    return R.success(data={
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


@router.post("/dataset", summary="执行版本化 RAG 题库评测")
async def eval_dataset(req: DatasetEvalRequest, user: TokenPayload = Depends(require_admin)):
    """执行题库回归评测，返回来源、拒答、关键事实和延迟等确定性指标。"""
    # 在 TenantMiddleware 注入上下文之后创建，确保评测只读取当前租户的知识库。
    summary = await EvaluationHistoryService().execute(
        tenant_id=user.tenant_id,
        operator_id=user.user_id,
        limit=req.limit,
    )
    return R.success(data=summary)


@router.get("/dataset/runs", summary="查询题库评测历史")
async def list_dataset_runs(
    limit: int = 10,
    user: TokenPayload = Depends(require_admin),
):
    """查询当前租户最近的评测运行，供质量趋势对比使用。"""
    bounded_limit = min(max(limit, 1), 50)
    items = await EvaluationHistoryService().list_runs(user.tenant_id, bounded_limit)
    return R.success(data={"items": items})


@router.get("/dataset/runs/{run_id}", summary="查询一次题库评测明细")
async def get_dataset_run(run_id: str, user: TokenPayload = Depends(require_admin)):
    """按运行 ID 回看汇总、检索参数快照和逐题明细。"""
    payload = await EvaluationHistoryService().get_run_detail(user.tenant_id, run_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="评测运行不存在或无权访问")
    return R.success(data=payload)
