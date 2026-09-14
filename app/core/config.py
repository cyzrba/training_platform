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

    #: 上传文件落盘位置，留空则用 <项目根>/data/uploads
    upload_dir: str = ""
    #: 单文件大小上限（MB）
    max_upload_mb: int = 50

    #: 文件存储后端：local 本地磁盘 / s3 对象存储（MinIO、OSS、S3 都走 s3 协议）
    storage_backend: str = "local"
    #: S3 兼容服务的接入信息（MinIO 部署时填 MinIO 地址）
    s3_endpoint: str = ""  # 如 http://127.0.0.1:9000
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "training-platform"
    s3_region: str = "us-east-1"
    #: 访问对象存储是否走系统/环境代理（MinIO 一般在内网，默认 False）
    s3_use_proxy: bool = False

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

    @property
    def resolved_upload_dir(self) -> Path:
        """上传文件根目录（本地存储时使用，首次取用即建好目录）。"""
        target = Path(self.upload_dir).expanduser() if self.upload_dir else DATA_DIR / "uploads"
        target.mkdir(parents=True, exist_ok=True)
        return target


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
