from logging.config import fileConfig
import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool
from dotenv import load_dotenv

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(project_root, ".env"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.db import Base, normalize_database_url
from app import models  # noqa: F401 - registers mapped tables with Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
if os.getenv("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", normalize_database_url(os.environ["DATABASE_URL"]).replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
