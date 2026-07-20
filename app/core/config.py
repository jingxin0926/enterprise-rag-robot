"""
应用配置加载模块

设计要点：
1. 使用 pydantic-settings 从 .env 文件加载配置
2. 支持多环境：通过 APP_ENV 切换 dev / test / prod
3. 配置项强类型校验，启动时即可发现配置错误（fail-fast）
4. 通过 lru_cache 实现单例，避免重复读取
"""

from enum import Enum
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（用于定位 .env 等相对路径）
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent


class AppEnv(str, Enum):
    """运行环境枚举"""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class AppSettings(BaseSettings):
    """
    应用级配置（从环境变量或 .env 文件加载）

    使用示例：
        from app.core.config import settings
        print(settings.app_name)
    """

    # ============================================================
    # Pydantic 配置：指定 .env 路径、编码、忽略未声明字段
    # ============================================================
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 中未在此类声明的字段
    )

    # ============================================================
    # 应用基础
    # ============================================================
    app_env: AppEnv = Field(default=AppEnv.DEV, description="运行环境")
    app_name: str = Field(default="smart-qa-system", description="应用名")
    app_version: str = Field(default="0.1.0", description="版本号")
    app_port: int = Field(default=8000, description="监听端口")
    app_debug: bool = Field(default=True, description="是否开启 Debug")

    # ============================================================
    # 日志
    # ============================================================
    log_level: str = Field(default="INFO", description="日志级别")
    log_dir: str = Field(default="logs", description="日志目录")
    log_console: bool = Field(default=True, description="是否输出到控制台")

    # ============================================================
    # DeepSeek 大模型
    # ============================================================
    deepseek_api_key: str = Field(default="", description="DeepSeek API Key")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="DeepSeek 接口地址",
    )
    deepseek_model: str = Field(default="deepseek-chat", description="默认模型")
    deepseek_temperature: float = Field(default=0.3, description="采样温度")
    deepseek_max_tokens: int = Field(default=2048, description="最大输出 token")
    deepseek_timeout: int = Field(default=60, description="请求超时（秒）")

    # ============================================================
    # Redis（P1 会话记忆）
    # ============================================================
    redis_host: str = Field(default="127.0.0.1", description="Redis 地址")
    redis_port: int = Field(default=6379, description="Redis 端口")
    redis_password: str = Field(default="", description="Redis 密码")
    redis_db: int = Field(default=0, description="Redis DB")

    # ============================================================
    # MySQL（知识库元数据）
    # ============================================================
    mysql_host: str = Field(default="", description="MySQL 地址")
    mysql_port: int = Field(default=3306, description="MySQL 端口")
    mysql_user: str = Field(default="smartqa", description="MySQL 应用账号")
    mysql_password: str = Field(default="", description="MySQL 应用账号密码")
    mysql_database: str = Field(default="smart_qa", description="MySQL 数据库名")

    # ============================================================
    # 异步文档任务
    # ============================================================
    document_task_max_retries: int = Field(default=3, ge=0, le=10, description="文档任务最大重试次数")

    # ============================================================
    # Qdrant 向量库
    # ============================================================
    qdrant_url: str = Field(default="", description="Qdrant Server URL，例如 http://qdrant:6333")
    qdrant_host: str = Field(default="", description="Qdrant Host（未配置 qdrant_url 时使用）")
    qdrant_port: int = Field(default=6333, description="Qdrant HTTP 端口")
    qdrant_api_key: str = Field(default="", description="Qdrant API Key（可选）")
    qdrant_local_path: str = Field(default="data/qdrant_storage", description="本地 Qdrant 文件存储路径")

    # ============================================================
    # RAG 检索质量门禁
    # ============================================================
    rag_vector_score_threshold: float = Field(
        default=0.3,
        ge=0,
        le=1,
        description="向量召回最低相似度，低于该值的片段不进入混合检索",
    )
    rag_strong_vector_score: float = Field(
        default=0.6,
        ge=0,
        le=1,
        description="单路向量证据可直接回答的强相似度阈值",
    )

    # ============================================================
    # 安全 / JWT
    # ============================================================
    jwt_secret_key: str = Field(
        default="please-change-this-secret-in-env",
        description="JWT 签名密钥（必须通过 .env 配置，禁止使用默认值上线）",
    )
    admin_init_password: str = Field(
        default="please-change-this-password-in-env",
        description="初始管理员密码（必须通过 .env 配置，禁止使用默认值上线）",
    )

    # ============================================================
    # 会话配置
    # ============================================================
    # 单次会话最大保留的历史轮数（一问一答算一轮）
    chat_max_history: int = Field(default=20, description="最大历史轮数")
    # 会话过期时间（秒），默认 2 小时
    chat_session_ttl: int = Field(default=7200, description="会话过期时间(秒)")

    # ============================================================
    # 派生属性
    # ============================================================
    @property
    def is_prod(self) -> bool:
        """是否为生产环境"""
        return self.app_env == AppEnv.PROD

    @property
    def is_dev(self) -> bool:
        """是否为开发环境"""
        return self.app_env == AppEnv.DEV

    @property
    def redis_url(self) -> str:
        """构造 Redis URL"""
        password_part = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def mysql_enabled(self) -> bool:
        """是否已配置可用的 MySQL 元数据存储。"""
        return bool(self.mysql_host and self.mysql_password)

    @property
    def mysql_url(self) -> str:
        """构造 SQLAlchemy asyncmy 连接串。"""
        if not self.mysql_enabled:
            return ""
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password)
        database = quote_plus(self.mysql_database)
        return f"mysql+asyncmy://{user}:{password}@{self.mysql_host}:{self.mysql_port}/{database}?charset=utf8mb4"

    @property
    def qdrant_server_url(self) -> str:
        """构造 Qdrant Server URL；未配置时返回空字符串，表示使用本地文件模式。"""
        if self.qdrant_url:
            return self.qdrant_url.rstrip("/")
        if self.qdrant_host:
            return f"http://{self.qdrant_host}:{self.qdrant_port}"
        return ""

    @property
    def use_qdrant_server(self) -> bool:
        """是否连接独立 Qdrant Server。"""
        return bool(self.qdrant_server_url)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """
    获取应用配置（单例）

    使用 lru_cache 实现进程内单例，全局只读取一次 .env
    """
    _settings = AppSettings()

    # Fail-fast：生产环境禁止使用默认 JWT 密钥
    if _settings.jwt_secret_key == "please-change-this-secret-in-env":
        if _settings.is_prod:
            raise RuntimeError(
                "🚨 JWT_SECRET_KEY 未配置！生产环境禁止使用默认密钥，请在 .env 或环境变量中设置一个强随机字符串。"
            )

    # Fail-fast：生产环境禁止使用默认管理员密码
    if _settings.admin_init_password == "please-change-this-password-in-env":
        if _settings.is_prod:
            raise RuntimeError(
                "🚨 ADMIN_INIT_PASSWORD 未配置！生产环境禁止使用默认密码，请在 .env 或环境变量中设置一个强密码。"
            )

    if _settings.is_prod and _settings.mysql_host and not _settings.mysql_password:
        raise RuntimeError("🚨 MYSQL_PASSWORD 未配置！生产环境禁止以空密码连接元数据存储。")

    return _settings


# 全局配置实例，业务代码直接 import 使用
settings: AppSettings = get_settings()
