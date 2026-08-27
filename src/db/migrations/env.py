import asyncio
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 04번 문서 원칙: Alembic으로 스키마 버전 관리. 스키마는 SQLAlchemy ORM
# 메타데이터가 아니라 04_db_schema.md의 원본 SQL을 그대로 옮긴 마이그레이션
# 파일로 관리한다(db/models/의 ORM 클래스는 §16.0-A용 — 선택 사항).
target_metadata = None


def get_database_url() -> str:
    """DATABASE_URL은 .env(01_data_models SecretBundle과 동일 목록)에서 읽는다."""
    project_root = Path(__file__).resolve().parents[3]
    env_values = dotenv_values(project_root / ".env")
    url = env_values.get("DATABASE_URL")
    if not url:
        raise RuntimeError(".env에 DATABASE_URL이 설정되어 있지 않습니다.")
    return url


def run_migrations_offline() -> None:
    """'offline' 모드 — Engine 없이 URL만으로 SQL을 출력한다."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """asyncpg 드라이버 사용 — Alembic 비동기 엔진 레시피(run_sync)로 실행."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
