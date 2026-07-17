"""MySQL 连接池与版本化迁移执行器。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import PROJECT_ROOT, settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_MIGRATION_DIR = PROJECT_ROOT / "db" / "migrations"


async def init_database() -> None:
    """初始化连接池并执行尚未应用的 SQL 迁移。"""
    global _engine, _session_factory

    if not settings.mysql_enabled:
        logger.warning("[MySQL] 未配置元数据存储，本地开发将跳过数据库初始化")
        return

    _engine = create_async_engine(
        settings.mysql_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    await _run_migrations(_engine)
    logger.info("[MySQL] 初始化完成 | database={}", settings.mysql_database)


async def close_database() -> None:
    """释放 MySQL 连接池。"""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def metadata_storage_enabled() -> bool:
    """返回元数据存储是否已启用。"""
    return _session_factory is not None


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """提供带事务边界的异步数据库会话。"""
    if _session_factory is None:
        raise RuntimeError("MySQL 元数据存储未启用，请配置 MYSQL_HOST 与 MYSQL_PASSWORD")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _run_migrations(engine: AsyncEngine) -> None:
    """按文件名顺序执行一次性 SQL 迁移。"""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migration (
                    version VARCHAR(128) NOT NULL PRIMARY KEY,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        )
        applied = {row[0] for row in (await connection.execute(text("SELECT version FROM schema_migration"))).all()}

        for migration in sorted(_MIGRATION_DIR.glob("V*.sql")):
            if migration.name in applied:
                continue

            statements = [statement.strip() for statement in migration.read_text(encoding="utf-8").split(";")]
            for statement in statements:
                if statement:
                    await connection.execute(text(statement))
            await connection.execute(
                text("INSERT INTO schema_migration(version) VALUES (:version)"),
                {"version": migration.name},
            )
            logger.info("[MySQL] 已应用迁移 | version={}", migration.name)
