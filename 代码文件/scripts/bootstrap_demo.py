"""初始化专属本机 MySQL 演示沙箱；仅将随机凭据保存到忽略提交的本地文件。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.init_analytics import initialize as initialize_analytics
from app.storage import Storage

RUNTIME = ROOT / ".runtime"
DEMO_FILE = RUNTIME / "demo-credentials.txt"
ROOT_FILE = RUNTIME / "root-credentials.txt"
ENV_FILE = ROOT / ".env"
ENV_MARKER = "# bootstrap_demo.py 管理的本机演示配置"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="初始化 127.0.0.1:3308 专属法律问数演示数据库；不打印密码。")
    result.add_argument("--root-url", default=os.environ.get("BOOTSTRAP_ROOT_URL"), help="SQLAlchemy/PyMySQL root URL；也可使用 BOOTSTRAP_ROOT_URL，避免命令行暴露密码")
    result.add_argument("--fixture-dir", type=Path, default=ROOT / "交付方案" / "微调数据", help="演示 schema.json 与 fixture_rows.json 所在目录")
    result.add_argument("--watermark", default=datetime.now().astimezone().isoformat(timespec="seconds"), help="演示数据同步水位；已有数据保持原水位")
    result.add_argument("--prepare-env", action="store_true", help="首次启动 Compose 前仅生成随机凭据与 .env，不连接数据库")
    return result


def mysql_url(username: str, password: str, database: str) -> str:
    return URL.create("mysql+pymysql", username=username, password=password, host="127.0.0.1", port=3308,
                      database=database, query={"charset": "utf8mb4"}).render_as_string(hide_password=False)


def save_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 排除其他 Unix 用户；Windows 沿用当前用户目录 ACL，文件不进入 Git。
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def local_credentials() -> dict:
    existing = load_json(DEMO_FILE)
    if existing is not None:
        return existing
    values = {"database_users": {"legal_app": secrets.token_urlsafe(32), "legal_reader": secrets.token_urlsafe(32)},
              "application_users": {"admin": secrets.token_urlsafe(24), "analyst": secrets.token_urlsafe(24)}}
    save_private(DEMO_FILE, json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    return values


def write_env(credentials: dict, root_password: str) -> None:
    if ENV_FILE.exists() and not ENV_FILE.read_text("utf-8").startswith(ENV_MARKER):
        return
    app_url = mysql_url("legal_app", credentials["database_users"]["legal_app"], "legal_app")
    reader_url = mysql_url("legal_reader", credentials["database_users"]["legal_reader"], "legal_analytics")
    content = f'''{ENV_MARKER}
# 仅限本地显式演示，MODEL_MODE=http 才连接实际模型服务。
MYSQL_ROOT_PASSWORD={root_password}
APP_DATABASE_URL={app_url}
ANALYTICS_DATABASE_URL={reader_url}
MODEL_MODE=mock
MODEL_BASE_URL=
MODEL_NAME=legal-sql-v1
MODEL_API_KEY=
MODEL_TIMEOUT_SECONDS=12
QUERY_TIMEOUT_SECONDS=10
REQUEST_TIMEOUT_SECONDS=30
MAX_ROWS=1000
MAX_RESULT_BYTES=1000000
COOKIE_SECURE=false
ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000
'''
    save_private(ENV_FILE, content)


def root_connection_url(argument: str | None) -> str:
    saved = load_json(ROOT_FILE)
    candidate = argument or (saved["root_url"] if saved else mysql_url("root", "", "mysql"))
    parsed = make_url(candidate)
    if parsed.drivername != "mysql+pymysql" or parsed.username != "root" or parsed.host not in {"127.0.0.1", "localhost"} or parsed.port != 3308:
        raise ValueError("仅允许 mysql+pymysql 的本机 root 连接，端口固定 3308；不能操作其他 MySQL")
    return parsed.set(database="mysql").render_as_string(hide_password=False)


def bootstrap(arguments: argparse.Namespace) -> None:
    root_url = root_connection_url(arguments.root_url)
    saved_root = load_json(ROOT_FILE)
    root_password = saved_root["password"] if saved_root else secrets.token_urlsafe(32)
    if arguments.prepare_env:
        credentials = local_credentials()
        write_env(credentials, root_password)
        save_private(ROOT_FILE, json.dumps({"password": root_password, "root_url": mysql_url("root", root_password, "mysql")}, indent=2) + "\n")
        print("配置已准备：.env；凭据：.runtime/demo-credentials.txt、.runtime/root-credentials.txt")
        return

    root_engine = create_engine(root_url, hide_parameters=True, pool_pre_ping=True)
    try:
        with root_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        had_credentials = DEMO_FILE.exists()
        credentials = local_credentials()
        with root_engine.begin() as connection:
            for schema in ("legal_app", "legal_analytics"):
                connection.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            for username in ("legal_app", "legal_reader"):
                exists = connection.execute(text("SELECT 1 FROM mysql.user WHERE User=:name AND Host='%'"), {"name": username}).first()
                if exists and not had_credentials:
                    raise ValueError("存在无法核对凭据的演示数据库账号，保留原账号并停止初始化")
                if not exists:
                    connection.execute(text(f"CREATE USER '{username}'@'%' IDENTIFIED BY :password"),
                                       {"password": credentials["database_users"][username]})
            connection.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, REFERENCES ON legal_app.* TO 'legal_app'@'%'"))

        analytics_url = make_url(root_url).set(database="legal_analytics").render_as_string(hide_password=False)
        analytics_engine = create_engine(analytics_url, hide_parameters=True)
        try:
            inspector = inspect(analytics_engine)
            tables, views = set(inspector.get_table_names()), set(inspector.get_view_names())
        finally:
            analytics_engine.dispose()
        expected_views = set(json.loads((arguments.fixture_dir / "schema.json").read_text("utf-8"))["tables"])
        if not tables and not views:
            initialize_analytics(analytics_url, arguments.fixture_dir, arguments.watermark)
        elif not expected_views.issubset(views) or "sync_watermark" not in tables:
            raise ValueError("分析 schema 已有不完整或未知数据，保留现状；请人工检查后重试")
        if len(expected_views) != 6 or not all(name.startswith("v_") and name.replace("_", "").isalnum() for name in expected_views):
            raise ValueError("演示分析必须包含批准的六个 v_ 视图")
        with root_engine.begin() as connection:
            show_view_grants = set(connection.execute(text("SELECT TABLE_NAME FROM information_schema.TABLE_PRIVILEGES "
                "WHERE GRANTEE=:grantee AND TABLE_SCHEMA='legal_analytics' AND PRIVILEGE_TYPE='SHOW VIEW'"),
                {"grantee": "'legal_reader'@'%'"}).scalars())
            for view in sorted(expected_views):
                connection.execute(text(f"GRANT SELECT ON legal_analytics.`{view}` TO 'legal_reader'@'%'"))
                if view in show_view_grants:
                    # EXPLAIN 仍要求底表权限；恢复最小授权，不用 SHOW VIEW 换取无效的预检能力。
                    connection.execute(text(f"REVOKE SHOW VIEW ON legal_analytics.`{view}` FROM 'legal_reader'@'%'"))
            connection.execute(text("GRANT SELECT ON legal_analytics.sync_watermark TO 'legal_reader'@'%'"))

        storage = Storage(mysql_url("legal_app", credentials["database_users"]["legal_app"], "legal_app"))
        try:
            storage.initialize()
            for username in ("admin", "analyst"):
                storage.seed_user(username, credentials["application_users"][username], username)
        finally:
            storage.engine.dispose()

        # 先落盘恢复信息，再关闭初始空密码；只修改此专属沙箱的两个标准 root 入口。
        save_private(ROOT_FILE, json.dumps({"password": root_password, "root_url": mysql_url("root", root_password, "mysql")}, indent=2) + "\n")
        with root_engine.begin() as connection:
            hosts = connection.execute(text("SELECT Host FROM mysql.user WHERE User='root' AND Host IN ('localhost','%')")).scalars().all()
            for host in hosts:
                connection.execute(text(f"ALTER USER 'root'@'{host}' IDENTIFIED BY :password"), {"password": root_password})
        write_env(credentials, root_password)
        print("演示数据库初始化成功；配置：.env；凭据：.runtime/demo-credentials.txt、.runtime/root-credentials.txt")
    finally:
        root_engine.dispose()


def main() -> int:
    arguments = parser().parse_args()
    try:
        bootstrap(arguments)
    except (SQLAlchemyError, ValueError, OSError, KeyError) as error:
        # 数据库异常可能包含认证 SQL 或连接 URL，不把异常原文写入终端日志。
        code = getattr(getattr(error, "orig", None), "args", (None,))[0]
        print(f"初始化未完成：{type(error).__name__}；数据库错误码：{code}。原数据与配置保留，请检查本地连接和凭据文件。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
