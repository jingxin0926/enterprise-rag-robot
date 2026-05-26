"""
Prompt 加载器

核心功能：
1. 从 .md 文件加载结构化 Prompt
2. 支持变量占位符替换（{variable} 语法）
3. 内置缓存，避免重复读取文件 IO
4. 支持热重载（开发模式下每次读最新文件）

使用示例：
    from app.prompts.loader import PromptLoader

    loader = PromptLoader()
    prompt = loader.load("rag_system", context="参考资料...", question="年假怎么请？")
"""

from pathlib import Path
from functools import lru_cache
from loguru import logger

# Prompt 文件所在目录
_PROMPTS_DIR = Path(__file__).parent


class PromptLoader:
    """
    Prompt 加载器

    职责：
    - 从 Markdown 文件加载 Prompt 模板
    - 执行变量替换
    - 缓存已加载的模板（生产模式）
    - 支持多租户 Prompt 定制（租户专属 → fallback 到默认）

    设计决策：
    - 用 .md 文件管理：支持 Git 版本追踪，方便协作和 Code Review
    - 用 {variable} 占位符：轻量无额外依赖，满足当前模板需求
    - 多租户：先查 prompts/tenants/{tenant_id}/ 目录，没有则用默认 prompts/
    """

    def __init__(self, prompts_dir: Path | None = None, cache_enabled: bool = True) -> None:
        """
        初始化加载器

        Args:
            prompts_dir: Prompt 文件目录，默认为本模块所在目录
            cache_enabled: 是否启用缓存（开发时可关闭，方便调试）
        """
        self._dir = prompts_dir or _PROMPTS_DIR
        self._cache_enabled = cache_enabled
        self._cache: dict[str, str] = {}

    def _read_file(self, name: str, tenant_id: str | None = None) -> str:
        """
        读取 Prompt 文件内容（支持多租户）

        查找顺序：
        1. prompts/tenants/{tenant_id}/{name}.md（租户专属）
        2. prompts/{name}.md（默认）

        Args:
            name: 文件名（不含 .md 后缀）
            tenant_id: 租户ID（可选）

        Returns:
            文件内容字符串

        Raises:
            FileNotFoundError: 默认文件也不存在时抛出
        """
        # 先查租户专属目录
        if tenant_id and tenant_id != "default":
            tenant_path = self._dir / "tenants" / tenant_id / f"{name}.md"
            if tenant_path.exists():
                logger.debug("[PromptLoader] 使用租户专属 Prompt: tenant={} name={}", tenant_id, name)
                return tenant_path.read_text(encoding="utf-8")

        # fallback 到默认目录
        file_path = self._dir / f"{name}.md"
        if not file_path.exists():
            raise FileNotFoundError(
                f"Prompt 文件不存在: {file_path}。"
                f"请在 {self._dir} 目录下创建 {name}.md 文件。"
            )
        return file_path.read_text(encoding="utf-8")

    def load(self, name: str, tenant_id: str | None = None, **kwargs: str) -> str:
        """
        加载并渲染 Prompt（支持多租户）

        Args:
            name: Prompt 名称（对应 prompts/ 目录下的文件名，不含 .md）
            tenant_id: 租户ID（可选，传入则优先加载租户专属 Prompt）
            **kwargs: 模板变量，如 context="...", question="..."

        Returns:
            渲染后的 Prompt 文本

        使用示例：
            loader.load("rag_system", tenant_id="company_a", context="文档内容...")
        """
        # 缓存 key 需要包含 tenant_id
        cache_key = f"{tenant_id or 'default'}:{name}"

        # 从缓存或文件读取模板
        if self._cache_enabled and cache_key in self._cache:
            template = self._cache[cache_key]
        else:
            template = self._read_file(name, tenant_id)
            if self._cache_enabled:
                self._cache[cache_key] = template
                logger.debug("[PromptLoader] 已缓存 Prompt: {}", cache_key)

        # 变量替换
        if kwargs:
            try:
                rendered = template.format(**kwargs)
            except KeyError as e:
                logger.warning(
                    "[PromptLoader] Prompt '{}' 中存在未提供的变量: {}",
                    name, e,
                )
                # 降级：不替换未提供的变量
                rendered = template
                for key, value in kwargs.items():
                    rendered = rendered.replace(f"{{{key}}}", value)
        else:
            rendered = template

        return rendered

    def reload(self, name: str | None = None) -> None:
        """
        清除缓存，强制下次从文件重新读取

        Args:
            name: 指定清除某个 Prompt 的缓存，None 表示清除全部
        """
        if name:
            self._cache.pop(name, None)
        else:
            self._cache.clear()
        logger.info("[PromptLoader] 缓存已清除: {}", name or "全部")

    def list_prompts(self) -> list[str]:
        """列出所有可用的 Prompt 文件"""
        return [f.stem for f in self._dir.glob("*.md")]


@lru_cache(maxsize=1)
def get_prompt_loader() -> PromptLoader:
    """
    获取全局 PromptLoader 单例

    用法：
        from app.prompts.loader import get_prompt_loader
        loader = get_prompt_loader()
        prompt = loader.load("rag_system", context=ctx)
    """
    return PromptLoader()
