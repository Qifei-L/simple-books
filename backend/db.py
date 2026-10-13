import os
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, String,
                        Boolean, DateTime, Text, ForeignKey)

url = os.getenv("DATABASE_URL", "sqlite:///./books.db")
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(url, pool_pre_ping=True,
                       connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
metadata = MetaData()
users = Table("app_users", metadata,
    Column("id", Integer, primary_key=True), Column("email", String(320), unique=True, nullable=False),
    Column("hashed_password", String(1024), nullable=False),
    Column("is_active", Boolean, default=True, nullable=False),
    Column("is_superuser", Boolean, default=False, nullable=False),
    Column("is_verified", Boolean, default=True, nullable=False),
    Column("role", String(20), default="Viewer", nullable=False))
tokens = Table("app_tokens", metadata,
    Column("token", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("app_users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False))
operations = Table("app_operations", metadata,
    Column("id", Integer, primary_key=True), Column("request_key", String(100), unique=True, nullable=False),
    Column("payload_hash", String(64), nullable=False), Column("transaction_id", Integer, unique=True),
    Column("kind", String(40), nullable=False), Column("actor_id", Integer, nullable=False),
    Column("due_date", String(10)), Column("created_at", DateTime, nullable=False),
    Column("related_id", Integer), Column("resolved", Boolean, default=False, nullable=False))
audit = Table("app_audit", metadata,
    Column("id", Integer, primary_key=True), Column("actor_id", Integer, nullable=False),
    Column("action", String(50), nullable=False), Column("detail", Text, nullable=False),
    Column("created_at", DateTime, nullable=False))
