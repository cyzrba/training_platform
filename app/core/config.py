"""应用配置：统一从 .env / 环境变量读取。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/core/config.py -> app/core -> app -> 根）
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "岗位闯关式实训平台"
    app_version: str = "0.1.0"
    api_prefix: str = "/api"
    debug: bool = True

    # 留空则使用 <项目根>/data/app.db
    database_url: str = ""
    sql_echo: bool = False

    cors_origins: list[str] = ["*"]

    #: 新建账号（用户 / 导入的学生）的默认密码，仅写入哈希后落库
    default_password: str = "123456"

    default_page_size: int = 20
    max_page_size: int = 200

    @property
    def sqlite_file(self) -> Path:
        """SQLite 数据库文件路径（仅数据库为 sqlite 时有意义）。"""
        prefix = "sqlite+aiosqlite:///"
        url = self.resolved_database_url
        if url.startswith(prefix):
            return Path(url[len(prefix) :])
        return DATA_DIR / "app.db"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        # 首次取用即建好 data 目录，避免 SQLite "unable to open database file"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{(DATA_DIR / 'app.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
