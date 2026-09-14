"""文件存储：本地磁盘 / S3 协议对象存储（MinIO、OSS、AWS S3 都走 s3）双后端。

存储约定（两个后端一致）：
- **项目相关**（报告模板、数据文件等）：``projects/<项目ID>-<项目名>/<用途>/<uuid>_<安全文件名>``，
  例如 ``projects/1-工业缺陷检测实训/report_template/3f1c…_实训报告模板.md``
  —— 一个实训项目一个文件夹，里面按用途分 dataset / guide / report_template / other；
- **其它文件**（证书、头像、学生作答附件等）：``misc/<biz_type>/<年月>/<uuid>_<安全文件名>``；
- 本地后端落在 ``<upload_dir>/<bucket>/...``，S3 后端就是对象的 Key；
- ``file_asset.bucket`` 存桶名、``object_key`` 存相对路径，两者拼起来定位文件；
- 同一份内容重复上传生成不同 object_key（uuid 前缀），不去重；``sha256`` 供业务查重。

后端由 ``settings.storage_backend`` 选择：
- ``local``：写本地磁盘（默认，开发与测试用，零外部依赖）；
- ``s3``：走 S3 协议，需要配置 S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY / S3_BUCKET。

本地存量文件迁到 MinIO：``uv run python scripts/migrate_files_to_s3.py``（见 README）。
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.exceptions import BusinessRuleError, NotFoundError

LOCAL_BUCKET = "local"
S3_BACKEND = "s3"
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]+")


@dataclass(frozen=True)
class StoredFile:
    """落盘/入桶结果：写进 file_asset 的几个字段。"""

    bucket: str
    object_key: str
    size_bytes: int
    sha256: str
    backend: str


def backend_name() -> str:
    return S3_BACKEND if settings.storage_backend.lower() == S3_BACKEND else "local"


def _safe_name(filename: str) -> str:
    """去掉路径分隔符与特殊字符，避免目录穿越和奇怪文件名。"""
    name = Path(filename).name.strip() or "unnamed"
    cleaned = _SAFE_NAME_RE.sub("_", name)
    return cleaned[:120] or "unnamed"


def _root() -> Path:
    return settings.resolved_upload_dir


# ------------------------------------------------------------------ S3 客户端


@lru_cache(maxsize=1)
def _s3_client() -> Any:
    """S3 兼容客户端（MinIO 需要 path style 寻址）。"""
    import boto3
    from botocore.config import Config

    if not settings.s3_endpoint or not settings.s3_access_key or not settings.s3_secret_key:
        raise BusinessRuleError("未配置 S3 接入信息（S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY）")
    config_kwargs: dict[str, Any] = {
        "signature_version": "s3v4",
        "s3": {"addressing_style": "path"},
    }
    if not settings.s3_use_proxy:
        # 默认不走代理：对象存储一般在内网，本机若开了全局代理会把请求转出去（表现为 502）。
        # 要访问需要代理的公网 S3，把 .env 的 S3_USE_PROXY 设为 true。
        config_kwargs["proxies"] = {}
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(**config_kwargs),
    )


def reset_client_cache() -> None:
    """改完配置（比如测试里切后端）后清掉客户端缓存。"""
    _s3_client.cache_clear()


def ensure_bucket(bucket: str | None = None) -> str:
    """确保桶存在（不存在就建），返回桶名。"""
    client = _s3_client()
    name = bucket or settings.s3_bucket
    try:
        client.head_bucket(Bucket=name)
    except Exception:  # 桶不存在或无权访问 → 尝试创建
        client.create_bucket(Bucket=name)
    return name


# -------------------------------------------------------------------- 写入


def _build_object_key(filename: str, biz_type: str) -> str:
    month = datetime.now(UTC).strftime("%Y%m")
    return f"misc/{biz_type.lower()}/{month}/{uuid4().hex}_{_safe_name(filename)}"


def project_scope(project_id: int, project_name: str) -> str:
    """项目的存储目录前缀：一个项目一个文件夹，名字带项目名方便在控制台里认。"""
    return f"projects/{project_id}-{_safe_name(project_name)}"


def save_bytes(content: bytes, *, filename: str, biz_type: str, scope: str | None = None) -> StoredFile:
    """把内容写到当前后端，返回 bucket / object_key / 大小 / 摘要。

    ``scope`` 传项目前缀（见 :func:`project_scope`）时按项目组织目录，否则落到 ``misc/``。
    """
    limit = settings.max_upload_mb * 1024 * 1024
    if len(content) > limit:
        raise BusinessRuleError(f"文件超过 {settings.max_upload_mb}MB 上限，请压缩后再上传")
    if not content:
        raise BusinessRuleError("文件内容为空")

    object_key = (
        f"{scope}/{biz_type.lower()}/{uuid4().hex}_{_safe_name(filename)}"
        if scope
        else _build_object_key(filename, biz_type)
    )
    digest = hashlib.sha256(content).hexdigest()

    if backend_name() == S3_BACKEND:
        bucket = ensure_bucket()
        _s3_client().put_object(Bucket=bucket, Key=object_key, Body=content)
        return StoredFile(
            bucket=bucket,
            object_key=object_key,
            size_bytes=len(content),
            sha256=digest,
            backend=S3_BACKEND,
        )

    target = _root() / LOCAL_BUCKET / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return StoredFile(
        bucket=LOCAL_BUCKET,
        object_key=object_key,
        size_bytes=len(content),
        sha256=digest,
        backend="local",
    )


# -------------------------------------------------------------------- 读取


def path_of(bucket: str, object_key: str) -> Path:
    """本地后端的磁盘路径；挡住 ``..`` 之类的越权访问。"""
    root = (_root() / bucket).resolve()
    target = (root / object_key).resolve()
    if root not in target.parents and target != root:
        raise NotFoundError("文件路径非法")
    if not target.is_file():
        raise NotFoundError(f"文件不存在：{bucket}/{object_key}")
    return target


def is_object_storage(bucket: str) -> bool:
    """这条台账记录是不是在对象存储里（本地桶名固定 local）。"""
    return bucket != LOCAL_BUCKET


def read_bytes(bucket: str, object_key: str) -> bytes:
    """读文件内容：对象存储走 S3，本地走磁盘。"""
    if is_object_storage(bucket):
        try:
            result = _s3_client().get_object(Bucket=bucket, Key=object_key)
        except Exception as exc:  # 对象不存在 / 权限不足
            raise NotFoundError(f"对象不存在：{bucket}/{object_key}") from exc
        return result["Body"].read()
    return path_of(bucket, object_key).read_bytes()


def exists(bucket: str, object_key: str) -> bool:
    try:
        if is_object_storage(bucket):
            _s3_client().head_object(Bucket=bucket, Key=object_key)
            return True
        return path_of(bucket, object_key).is_file()
    except Exception:
        return False


# -------------------------------------------------------------------- 删除


def delete(bucket: str, object_key: str) -> None:
    """删除文件；文件不在（比如已被清理）时静默跳过。"""
    if is_object_storage(bucket):
        try:
            _s3_client().delete_object(Bucket=bucket, Key=object_key)
        except Exception:
            return
        return
    try:
        path_of(bucket, object_key).unlink()
    except NotFoundError:
        return


def upload_local_file(local_path: Path, *, bucket: str, object_key: str) -> None:
    """把本地磁盘上的文件搬到对象存储（迁移脚本用），Key 保持不变。"""
    client = _s3_client()
    with local_path.open("rb") as handle:
        client.upload_fileobj(handle, bucket, object_key)


def move_object(bucket: str, source_key: str, target_key: str) -> None:
    """在同一个桶里移动对象（重新组织目录用）：对象存储用 copy+delete，本地用重命名。"""
    if source_key == target_key:
        return
    if is_object_storage(bucket):
        client = _s3_client()
        client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": source_key}, Key=target_key)
        client.delete_object(Bucket=bucket, Key=source_key)
        return
    source = path_of(bucket, source_key)
    target = _root() / bucket / target_key
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


__all__ = [
    "LOCAL_BUCKET",
    "S3_BACKEND",
    "StoredFile",
    "backend_name",
    "delete",
    "ensure_bucket",
    "exists",
    "is_object_storage",
    "move_object",
    "path_of",
    "project_scope",
    "read_bytes",
    "reset_client_cache",
    "save_bytes",
    "upload_local_file",
]
