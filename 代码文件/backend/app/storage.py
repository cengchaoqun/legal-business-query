"""应用状态存储。业务分析连接与此处的认证、会话数据库相互独立。"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, MetaData,
    String, Table, Text, UniqueConstraint, create_engine, delete, select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool


class StorageConflict(Exception):
    """归属不符、幂等键重复或会话已在处理另一请求。"""


def _now() -> datetime:
    # 数据库统一保存 UTC 无时区时间，避免 MySQL DATETIME 丢弃时区产生歧义。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _public(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_public(item) for item in value]
    return value


class Storage:
    SESSION_TTL_SECONDS = 8 * 60 * 60
    MAX_SNAPSHOT_BYTES = 1024 * 1024
    TERMINAL_STATUSES = frozenset({"clarify", "refuse", "succeeded", "failed"})
    STAGES = frozenset({"received", "retrieving", "generating", "validating", "executing", "formatting"})

    def __init__(self, database_url: str):
        options: dict[str, Any] = {"pool_pre_ping": True, "hide_parameters": True}
        if database_url.startswith("mysql"):
            # 应用库同样需要网络超时，否则数据库失联时登录和历史会一直等待握手。
            options["connect_args"] = {"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10}
        if database_url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if database_url in {"sqlite://", "sqlite:///:memory:"}:
                options["poolclass"] = StaticPool
        self.engine = create_engine(database_url, **options)
        self.metadata = MetaData()
        self.users = Table(
            "app_user", self.metadata,
            Column("id", String(36), primary_key=True),
            Column("username", String(100), unique=True, nullable=False),
            Column("password_hash", String(256), nullable=False),
            Column("role", String(20), nullable=False),
            Column("organization_id", String(100), nullable=False),
            Column("scope_id", String(100), nullable=False),
            Column("created_at", DateTime, nullable=False),
        )
        self.sessions = Table(
            "auth_session", self.metadata,
            Column("token_hash", String(64), primary_key=True),
            Column("user_id", String(36), ForeignKey("app_user.id"), nullable=False),
            Column("created_at", DateTime, nullable=False),
            Column("expires_at", DateTime, nullable=False, index=True),
        )
        self.conversations = Table(
            "conversation", self.metadata,
            Column("id", String(36), primary_key=True),
            Column("user_id", String(36), ForeignKey("app_user.id"), nullable=False, index=True),
            Column("title", String(200), nullable=False),
            Column("confirmed_conditions", JSON, nullable=False),
            Column("pending_clarification", JSON),
            Column("active_query_id", String(36)),
            Column("created_at", DateTime, nullable=False),
            Column("updated_at", DateTime, nullable=False),
        )
        self.queries = Table(
            "query_run", self.metadata,
            Column("id", String(36), primary_key=True),
            Column("conversation_id", String(36), ForeignKey("conversation.id"), nullable=False, index=True),
            Column("user_id", String(36), ForeignKey("app_user.id"), nullable=False),
            Column("client_request_id", String(128), nullable=False),
            Column("parent_query_id", String(36)),
            Column("question", Text, nullable=False),
            Column("status", String(20), nullable=False),
            Column("stage", String(30), nullable=False),
            Column("decision", JSON),
            Column("sql", Text),
            Column("result", JSON),
            Column("confirmed_conditions", JSON, nullable=False),
            Column("metadata_version", String(100)),
            Column("model_service_version", String(100)),
            Column("scope_id", String(100), nullable=False),
            Column("data_watermark", JSON),
            Column("result_snapshot_available", Boolean, nullable=False),
            Column("elapsed_ms", Integer, nullable=False),
            Column("error_code", String(100)),
            Column("error_message", Text),
            Column("created_at", DateTime, nullable=False),
            Column("deadline_at", DateTime, nullable=False),
            Column("finished_at", DateTime),
            UniqueConstraint("user_id", "client_request_id", name="uq_query_user_request"),
            Index("ix_query_expiry", "status", "deadline_at"),
        )
        self.feedback = Table(
            "query_feedback", self.metadata,
            Column("id", String(36), primary_key=True),
            Column("query_id", String(36), ForeignKey("query_run.id"), nullable=False),
            Column("user_id", String(36), ForeignKey("app_user.id"), nullable=False),
            Column("feedback_type", String(40), nullable=False),
            Column("comment", Text, nullable=False),
            Column("review_status", String(30), nullable=False),
            Column("created_at", DateTime, nullable=False),
        )

    def initialize(self) -> None:
        self.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            self._expire(connection)
            connection.execute(delete(self.sessions).where(self.sessions.c.expires_at <= _now()))

    @staticmethod
    def _user(row) -> dict | None:
        if row is None:
            return None
        return {key: row[key] for key in ("id", "username", "role", "organization_id", "scope_id")}

    @staticmethod
    def _query(row) -> dict | None:
        if row is None:
            return None
        result = _public(dict(row))
        result["query_id"] = result["id"]
        return result

    def seed_user(self, username: str, password: str, role: str, organization_id: str = "demo") -> dict:
        if role not in {"analyst", "admin"}:
            raise ValueError("角色必须是 analyst 或 admin")
        with self.engine.connect() as connection:
            existing = connection.execute(select(self.users).where(self.users.c.username == username)).mappings().first()
        if existing:
            return self._user(existing)
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 600000).hex()
        values = dict(id=str(uuid4()), username=username, password_hash=f"pbkdf2_sha256$600000${salt}${digest}", role=role,
                      organization_id=organization_id, scope_id="demo", created_at=_now())
        try:
            with self.engine.begin() as connection:
                connection.execute(self.users.insert().values(**values))
        except IntegrityError:
            # 多个 worker 同时启动时，只保留第一个已提交的预置账号。
            with self.engine.connect() as connection:
                return self._user(connection.execute(select(self.users).where(self.users.c.username == username)).mappings().one())
        return self._user(values)

    def authenticate(self, username: str, password: str) -> dict | None:
        with self.engine.connect() as connection:
            user = connection.execute(select(self.users).where(self.users.c.username == username)).mappings().first()
        # 未知账号也执行同成本派生，减少用户名存在性时序泄漏。
        stored = user["password_hash"] if user else "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32
        try:
            algorithm, rounds, salt, expected = stored.split("$")
            if algorithm != "pbkdf2_sha256":
                return None
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)).hex()
            valid = hmac.compare_digest(expected, actual)
        except (ValueError, TypeError):
            return None
        return self._user(user) if valid and user else None

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        with self.engine.begin() as connection:
            if connection.execute(select(self.users.c.id).where(self.users.c.id == user_id)).first() is None:
                raise StorageConflict("用户不存在")
            connection.execute(self.sessions.insert().values(token_hash=self._token_hash(token), user_id=user_id,
                created_at=now, expires_at=now + timedelta(seconds=self.SESSION_TTL_SECONDS)))
        return token

    def get_session(self, token: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.users).join(self.sessions, self.users.c.id == self.sessions.c.user_id)
                .where(self.sessions.c.token_hash == self._token_hash(token), self.sessions.c.expires_at > _now())).mappings().first()
        return self._user(row)

    def delete_session(self, token: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(self.sessions).where(self.sessions.c.token_hash == self._token_hash(token)))

    def create_conversation(self, user_id: str) -> dict:
        now = _now()
        values = dict(id=str(uuid4()), user_id=user_id, title="新建问数", confirmed_conditions={},
                      pending_clarification=None, active_query_id=None, created_at=now, updated_at=now)
        with self.engine.begin() as connection:
            if connection.execute(select(self.users.c.id).where(self.users.c.id == user_id)).first() is None:
                raise StorageConflict("用户不存在")
            connection.execute(self.conversations.insert().values(**values))
        return _public(values)

    def _expire(self, connection, conversation_id: str | None = None) -> None:
        now = _now()
        predicate = [self.queries.c.status == "processing", self.queries.c.deadline_at <= now]
        if conversation_id:
            predicate.append(self.queries.c.conversation_id == conversation_id)
        rows = connection.execute(select(self.queries.c.id, self.queries.c.conversation_id).where(*predicate)
            .order_by(self.queries.c.id)).mappings().all()
        for row in rows:
            changed = connection.execute(update(self.queries).where(self.queries.c.id == row["id"], *predicate).values(
                status="failed", error_code="QUERY_TIMEOUT", error_message="请求超过处理时限，请重新发起查询。", finished_at=now))
            if changed.rowcount:
                connection.execute(update(self.conversations).where(self.conversations.c.id == row["conversation_id"],
                    self.conversations.c.active_query_id == row["id"]).values(active_query_id=None, updated_at=now))

    def get_conversation(self, conversation_id: str, user_id: str) -> dict | None:
        with self.engine.begin() as connection:
            self._expire(connection, conversation_id)
            row = connection.execute(select(self.conversations).where(self.conversations.c.id == conversation_id,
                self.conversations.c.user_id == user_id)).mappings().first()
        return _public(dict(row)) if row else None

    def list_conversations(self, user_id: str) -> list[dict]:
        with self.engine.begin() as connection:
            self._expire(connection)
            rows = connection.execute(select(self.conversations).where(self.conversations.c.user_id == user_id)
                .order_by(self.conversations.c.updated_at.desc(), self.conversations.c.id)).mappings().all()
        return [_public(dict(row)) for row in rows]

    def list_queries(self, conversation_id: str) -> list[dict]:
        with self.engine.begin() as connection:
            self._expire(connection, conversation_id)
            rows = connection.execute(select(self.queries).where(self.queries.c.conversation_id == conversation_id)
                .order_by(self.queries.c.created_at, self.queries.c.id)).mappings().all()
        return [self._query(row) for row in rows]

    def get_query(self, query_id: str) -> dict | None:
        with self.engine.begin() as connection:
            self._expire(connection)
            row = connection.execute(select(self.queries).where(self.queries.c.id == query_id)).mappings().first()
        return self._query(row)

    def _request(self, connection, user_id, client_request_id):
        statement = select(self.queries).where(self.queries.c.user_id == user_id,
                                               self.queries.c.client_request_id == client_request_id)
        return connection.execute(statement).mappings().first()

    def _existing_request(self, row, conversation_id):
        if row["conversation_id"] != conversation_id:
            raise StorageConflict("client_request_id 已用于其他会话")
        return self._query(row), False

    def begin_query(self, conversation_id: str, user_id: str, client_request_id: str,
                    question: str, timeout_seconds: float = 30) -> tuple[dict, bool]:
        if not client_request_id or len(client_request_id) > 128:
            raise ValueError("client_request_id 长度必须在 1 至 128 字符之间")
        query_id, now = str(uuid4()), _now()
        try:
            with self.engine.begin() as connection:
                self._expire(connection, conversation_id)
                existing = self._request(connection, user_id, client_request_id)
                if existing:
                    return self._existing_request(existing, conversation_id)
                # 条件 UPDATE 是数据库中的互斥点；不能用进程锁代替，多 worker 必须共享此约束。
                acquired = connection.execute(update(self.conversations).where(self.conversations.c.id == conversation_id,
                    self.conversations.c.user_id == user_id, self.conversations.c.active_query_id.is_(None))
                    .values(active_query_id=query_id, updated_at=now))
                if acquired.rowcount != 1:
                    raise StorageConflict("会话不存在、不属于当前用户或正在处理另一个请求")
                conversation = connection.execute(select(self.conversations).where(self.conversations.c.id == conversation_id)
                    .with_for_update()).mappings().one()
                user = connection.execute(select(self.users).where(self.users.c.id == user_id)).mappings().one()
                previous = connection.execute(select(self.queries.c.id).where(self.queries.c.conversation_id == conversation_id)
                    .order_by(self.queries.c.created_at.desc(), self.queries.c.id.desc()).limit(1)).scalar()
                values = dict(id=query_id, conversation_id=conversation_id, user_id=user_id, client_request_id=client_request_id,
                    parent_query_id=previous, question=question, status="processing", stage="received", decision=None,
                    sql=None, result=None, confirmed_conditions=conversation["confirmed_conditions"], metadata_version=None,
                    model_service_version=None, scope_id=user["scope_id"], data_watermark=None, result_snapshot_available=True,
                    elapsed_ms=0, error_code=None, error_message=None, created_at=now,
                    deadline_at=now + timedelta(seconds=timeout_seconds), finished_at=None)
                connection.execute(self.queries.insert().values(**values))
                if conversation["title"] == "新建问数":
                    connection.execute(update(self.conversations).where(self.conversations.c.id == conversation_id).values(title=question[:200]))
                return self._query(values), True
        except (IntegrityError, StorageConflict):
            # 先退出持有会话锁的事务，再读取胜出的幂等请求，避免与 finish_query 形成反向锁顺序。
            # 新事务也保证 MySQL 默认 REPEATABLE READ 能读到另一 worker 刚提交的请求。
            with self.engine.connect() as connection:
                existing = self._request(connection, user_id, client_request_id)
            if existing:
                return self._existing_request(existing, conversation_id)
            raise

    def finish_query(self, query_id: str, status: str, decision: dict | None = None,
                     result: dict | None = None, conditions: dict | None = None,
                     error_code: str | None = None, error_message: str | None = None,
                     metadata_version: str | None = None, model_service_version: str | None = None,
                     elapsed_ms: int = 0) -> dict | None:
        if status not in self.TERMINAL_STATUSES:
            raise ValueError("无效的查询终态")
        snapshot_available = result is None or len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= self.MAX_SNAPSHOT_BYTES
        now = _now()
        with self.engine.begin() as connection:
            self._expire(connection)
            row = connection.execute(select(self.queries).where(self.queries.c.id == query_id).with_for_update()).mappings().first()
            if row is None or row["status"] != "processing":
                return self._query(row)
            confirmed = conditions if conditions is not None else row["confirmed_conditions"]
            values = dict(status=status, decision=decision, sql=decision.get("sql") if decision else None,
                result=result if snapshot_available else None, result_snapshot_available=snapshot_available,
                confirmed_conditions=confirmed, metadata_version=metadata_version, model_service_version=model_service_version,
                data_watermark=result.get("data_watermark") if isinstance(result, dict) else None,
                error_code=error_code, error_message=error_message, elapsed_ms=elapsed_ms, finished_at=now)
            changed = connection.execute(update(self.queries).where(self.queries.c.id == query_id,
                self.queries.c.status == "processing").values(**values))
            if changed.rowcount:
                conversation_values = dict(active_query_id=None, updated_at=now,
                    pending_clarification=decision if status == "clarify" else None)
                if conditions is not None:
                    conversation_values["confirmed_conditions"] = conditions
                connection.execute(update(self.conversations).where(self.conversations.c.id == row["conversation_id"],
                    self.conversations.c.active_query_id == query_id).values(**conversation_values))
            return self._query({**dict(row), **values})

    def update_stage(self, query_id: str, stage: str) -> None:
        if stage not in self.STAGES:
            raise ValueError("无效的处理阶段")
        with self.engine.begin() as connection:
            self._expire(connection)
            connection.execute(update(self.queries).where(self.queries.c.id == query_id,
                self.queries.c.status == "processing").values(stage=stage))

    def all_query_runs(self) -> list[dict]:
        with self.engine.begin() as connection:
            self._expire(connection)
            rows = connection.execute(select(self.queries).order_by(self.queries.c.created_at.desc(), self.queries.c.id)).mappings().all()
        return [self._query(row) for row in rows]

    def add_feedback(self, query_id: str, user_id: str, feedback_type: str, comment: str) -> dict:
        values = dict(id=str(uuid4()), query_id=query_id, user_id=user_id, feedback_type=feedback_type,
                      comment=comment, review_status="pending", created_at=_now())
        with self.engine.begin() as connection:
            owned = connection.execute(select(self.queries.c.id).where(self.queries.c.id == query_id,
                self.queries.c.user_id == user_id)).first()
            if owned is None:
                raise StorageConflict("查询不存在或不属于当前用户")
            connection.execute(self.feedback.insert().values(**values))
        return _public(values)
