"""
Prompt 在线管理接口

功能：
1. GET  /api/v1/prompts          — 列出所有 Prompt 文件
2. GET  /api/v1/prompts/{name}   — 查看某个 Prompt 内容
3. PUT  /api/v1/prompts/{name}   — 在线编辑 Prompt 内容
4. POST /api/v1/prompts/reload   — 刷新 Prompt 缓存（编辑后生效）

权限：仅 admin 角色可操作
安全：name 参数做白名单校验，防止路径穿越
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.exceptions import BizException, ErrorCode
from app.core.response import R
from app.core.security import TokenPayload
from app.middleware.trace import get_trace_id
from app.prompts.loader import get_prompt_loader

router = APIRouter(prefix="/prompts", tags=["Prompt管理"])

# Prompt 文件目录
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Prompt 名称白名单正则（只允许字母、数字、下划线、短横线）
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _require_admin(user: TokenPayload) -> None:
    """校验管理员角色，非 admin 直接拒绝"""
    if user.role != "admin":
        raise BizException(ErrorCode.FORBIDDEN, "该操作仅允许管理员执行")


def _validate_prompt_name(name: str) -> Path:
    """
    校验 Prompt 名称合法性 + 路径归一化

    防止路径穿越攻击（如 ../../etc/passwd）
    """
    if not _NAME_PATTERN.match(name):
        raise BizException(
            ErrorCode.PARAM_INVALID,
            f"Prompt 名称不合法（只允许字母、数字、下划线、短横线）: {name}",
        )

    file_path = (_PROMPTS_DIR / f"{name}.md").resolve()

    # 确保解析后的路径仍在 prompts 目录内（防止 symlink 穿越）
    if not str(file_path).startswith(str(_PROMPTS_DIR.resolve())):
        raise BizException(ErrorCode.PARAM_INVALID, "非法路径")

    return file_path


class PromptUpdateRequest(BaseModel):
    """Prompt 编辑请求"""

    content: str = Field(..., min_length=10, description="新的 Prompt 内容（Markdown格式）")


@router.get("", summary="列出所有 Prompt")
async def list_prompts(user: TokenPayload = Depends(get_current_user)):
    """
    返回所有可用的 Prompt 文件列表（仅 admin）
    """
    _require_admin(user)
    loader = get_prompt_loader()
    prompts = loader.list_prompts()
    return R.success(
        data={"prompts": prompts, "total": len(prompts)},
        trace_id=get_trace_id(),
    )


@router.get("/{name}", summary="查看 Prompt 内容")
async def get_prompt(name: str, user: TokenPayload = Depends(get_current_user)):
    """
    查看某个 Prompt 的完整内容（仅 admin）

    Args:
        name: Prompt 名称（不含 .md 后缀），如 rag_system、agent_system
    """
    _require_admin(user)
    file_path = _validate_prompt_name(name)

    if not file_path.exists():
        return R.fail(code=404, message=f"Prompt '{name}' 不存在", trace_id=get_trace_id())

    content = file_path.read_text(encoding="utf-8")
    return R.success(
        data={"name": name, "content": content},
        trace_id=get_trace_id(),
    )


@router.put("/{name}", summary="编辑 Prompt 内容")
async def update_prompt(name: str, req: PromptUpdateRequest, user: TokenPayload = Depends(get_current_user)):
    """
    在线编辑 Prompt 内容（仅 admin）

    编辑后会自动刷新缓存，下次 LLM 调用即使用新版 Prompt。

    Args:
        name: Prompt 名称（不含 .md 后缀）
    """
    _require_admin(user)
    file_path = _validate_prompt_name(name)

    if not file_path.exists():
        return R.fail(code=404, message=f"Prompt '{name}' 不存在", trace_id=get_trace_id())

    # 写入文件
    file_path.write_text(req.content, encoding="utf-8")

    # 刷新缓存
    loader = get_prompt_loader()
    loader.reload(name)

    logger.info("[PromptManage] Prompt '{}' 已更新并刷新缓存 | operator={}", name, user.username)

    return R.success(
        data={"name": name, "message": "更新成功，已自动刷新缓存"},
        message=f"Prompt '{name}' 更新成功",
        trace_id=get_trace_id(),
    )


@router.post("/reload", summary="刷新 Prompt 缓存")
async def reload_prompts(name: str | None = None, user: TokenPayload = Depends(get_current_user)):
    """
    手动刷新 Prompt 缓存（仅 admin）

    Args:
        name: 指定刷新某个 Prompt，不传则刷新全部
    """
    _require_admin(user)

    # 如果指定了 name，校验合法性
    if name:
        _validate_prompt_name(name)

    loader = get_prompt_loader()
    loader.reload(name)

    msg = f"已刷新: {name}" if name else "已刷新全部 Prompt 缓存"
    logger.info("[PromptManage] {} | operator={}", msg, user.username)

    return R.success(data={"message": msg}, trace_id=get_trace_id())
