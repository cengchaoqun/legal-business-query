from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', env_file_encoding='utf-8')
    app_database_url: str = 'mysql+pymysql://legal_app@127.0.0.1:3308/legal_app'
    analytics_database_url: str = 'mysql+pymysql://legal_reader@127.0.0.1:3308/legal_analytics'
    model_mode: Literal['http', 'mock'] = 'http'
    model_base_url: str = ''
    model_name: str = 'legal-sql-v1'
    model_api_key: str = ''
    # 按服务能力显式开启；替换为普通微调服务时可关闭扩展参数。
    model_json_mode: bool = False
    model_thinking: Literal['enabled', 'disabled'] | None = None
    model_timeout_seconds: float = Field(12, gt=0, le=30)
    query_timeout_seconds: float = Field(10, gt=0, le=10)
    request_timeout_seconds: float = Field(30, gt=0, le=60)
    max_rows: int = Field(1000, ge=1, le=1000)
    max_result_bytes: int = Field(1000000, ge=128, le=5000000)
    metadata_dir: Path = ROOT / '.runtime' / 'metadata'
    cookie_secure: bool = False
    allowed_origins: str = 'http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000'

