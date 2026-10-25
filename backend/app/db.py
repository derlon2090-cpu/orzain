"""SQLAlchemy database configuration for the paper-trading persistence slice."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return database_url


def make_engine(url: str | None = None):
    database_url = normalize_database_url(url or os.getenv("DATABASE_URL", "sqlite:///./tradegen.db"))
    options = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            options["poolclass"] = StaticPool
    return create_engine(database_url, **options)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session():
    with SessionLocal() as session:
        yield session
