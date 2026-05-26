"""
Prompt 在线管理接口

功能：
1. GET  /api/v1/prompts          — 列出所有 Prompt 文件
2. GET  /api/v1/prompts/{name}   — 查看某个 Prompt 内容
3. PUT  /api/v1/prompts/{name}   — 在线编辑 Prompt 内容
4. POST /api/v1/prompts/reload   — 刷新 Prompt 缓存（编辑后生效）

用途：
- 运营/测试人员可通过接口（或后续管理页面）直接修改 Prompt
- 修改后调用 reload 即时生效，无需重启服务、无需改代码
- 修改历史通过 Git 追踪（文件依然在磁盘上）
"""

from pathlib import Path

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from app.core.response import R
from app.middleware.trace import get_trace_id
from app.prompts.loader import get_prompt_loader

router = APIRouter(prefix="/prompts", tags=["Prompt管理"])

# Prompt 文件目录
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


class PromptUpdateRequest(BaseModel):
    """Prompt 编辑请求"""

    content: str = Field(..., min_length=10, description="新的 Prompt 内容（Markdown格式）")


@router.get("", summary="列出所有 Prompt")
async def list_prompts():
    """
    返回所有可用的 Prompt 文件列表

    运营/测试人员可以先调这个接口看有哪些 Prompt 可以编辑
    """
    loader = get_prompt_loader()
    prompts = loader.list_prompts()
    return R.success(
        data={"prompts": prompts, "total": len(prompts)},
        trace_id=get_trace_id(),
    )


@router.get("/{name}", summary="查看 Prompt 内容")
async def get_prompt(name: str):
    """
    查看某个 Prompt 的完整内容

    Args:
        name: Prompt 名称（不含 .md 后缀），如 rag_system、agent_system
    """
    file_path = _PROMPTS_DIR / f"{name}.md"
    if not file_path.exists():
        return R.fail(code=404, message=f"Prompt '{name}' 不存在", trace_id=get_trace_id())

    content = file_path.read_text(encoding="utf-8")
    return R.success(
        data={"name": name, "content": content, "file_path": str(file_path)},
        trace_id=get_trace_id(),
    )


@router.put("/{name}", summary="编辑 Prompt 内容")
async def update_prompt(name: str, req: PromptUpdateRequest):
    """
    在线编辑 Prompt 内容

    编辑后会自动刷新缓存，下次 LLM 调用即使用新版 Prompt。
    文件变更可通过 Git 追踪历史。

    Args:
        name: Prompt 名称（不含 .md 后缀）
    """
    file_path = _PROMPTS_DIR / f"{name}.md"
    if not file_path.exists():
        return R.fail(code=404, message=f"Prompt '{name}' 不存在", trace_id=get_trace_id())

    # 写入文件
    file_path.write_text(req.content, encoding="utf-8")

    # 刷新缓存
    loader = get_prompt_loader()
    loader.reload(name)

    logger.info("[PromptManage] Prompt '{}' 已更新并刷新缓存", name)

    return R.success(
        data={"name": name, "message": "更新成功，已自动刷新缓存"},
        message=f"Prompt '{name}' 更新成功",
        trace_id=get_trace_id(),
    )


@router.post("/reload", summary="刷新 Prompt 缓存")
async def reload_prompts(name: str | None = None):
    """
    手动刷新 Prompt 缓存

    场景：如果有人直接在服务器上改了 .md 文件（没走 API），
    调这个接口让系统重新从磁盘读取最新内容。

    Args:
        name: 指定刷新某个 Prompt，不传则刷新全部
    """
    loader = get_prompt_loader()
    loader.reload(name)

    msg = f"已刷新: {name}" if name else "已刷新全部 Prompt 缓存"
    logger.info("[PromptManage] {}", msg)

    return R.success(data={"message": msg}, trace_id=get_trace_id())
