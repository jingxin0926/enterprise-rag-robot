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


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """
    获取应用配置（单例）

    使用 lru_cache 实现进程内单例，全局只读取一次 .env
    """
    return AppSettings()


# 全局配置实例，业务代码直接 import 使用
settings: AppSettings = get_settings()
