"""
日志模块

设计要点：
1. 基于 loguru，配置极简、性能优秀
2. 同时输出控制台 + 文件，文件按天切割，保留 30 天
3. 生产环境采用 JSON 结构化日志，便于 ELK / Loki 采集
4. 拦截标准 logging（uvicorn / httpx 等三方库的日志会一并接管）
"""

import logging
import sys
from pathlib import Path

from loguru import logger

from app.core.config import settings


class _InterceptHandler(logging.Handler):
    """
    将标准库 logging 的日志转发到 loguru

    背景：FastAPI / uvicorn / httpx 等使用标准 logging，
    需要拦截后统一交给 loguru 处理，否则日志格式不一致。
    """

    def emit(self, record: logging.LogRecord) -> None:
        # 尝试映射日志级别
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)

        # 找到调用者真实位置（跳过 logging 内部帧）
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logger() -> None:
    """
    初始化日志配置

    在应用启动时调用一次即可。
    """
    # 1. 移除 loguru 默认 handler
    logger.remove()

    # 2. 控制台输出（开发友好，带颜色）
    if settings.log_console:
        logger.add(
            sys.stdout,
            level=settings.log_level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            colorize=True,
            backtrace=True,  # 异常时打印完整调用栈
            diagnose=settings.is_dev,  # 仅 dev 环境打印变量值，避免泄露敏感信息
        )

    # 3. 文件输出（按天切割，保留 30 天，自动压缩）
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        level=settings.log_level,
        rotation="00:00",  # 每天 0 点切割
        retention="30 days",  # 保留 30 天
        compression="zip",  # 历史日志压缩
        encoding="utf-8",
        enqueue=True,  # 异步写入，避免阻塞主线程
        # 生产环境用 JSON 格式（ELK 友好），开发环境用易读文本格式
        serialize=settings.is_prod,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} | {message}"
        ),
    )

    # 4. 错误级别单独存一个文件，方便排查问题
    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        level="ERROR",
        rotation="00:00",
        retention="60 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # 5. 拦截标准 logging（uvicorn / fastapi / httpx 等）
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "httpx"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [_InterceptHandler()]
        std_logger.propagate = False

    logger.info(
        "✅ 日志初始化完成 | env={} level={} dir={}",
        settings.app_env.value,
        settings.log_level,
        log_dir.absolute(),
    )


# 对外导出 logger 实例，业务代码直接 from app.core.logger import logger
__all__ = ["logger", "setup_logger"]
