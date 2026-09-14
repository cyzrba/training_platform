"""文件存储：S3 协议对象存储（MinIO / 阿里云 OSS / AWS S3 都走这一套）。

存储约定：
- **项目相关**（报告模板、数据文件等）：``projects/<项目ID>-<项目名>/<用途>/<uuid>_<安全文件名>``，
  例如 ``projects/1-工业缺陷检测实训/report_template/3f1c…_实训报告模板.md``
  —— 一个实训项目一个文件夹，里面按用途分 dataset / guide / report_template / other；
- **其它文件**（证书、头像、学生作答附件等）：``misc/<biz_type>/<年月>/<uuid>_<安全文件名>``；
- 对象的 Key 就是上面这串相对路径；``file_asset.bucket`` 存桶名、``object_key`` 存 Key，
  两者拼起来定位对象；
- 同一份内容重复上传生成不同 object_key（uuid 前缀），不去重；``sha256`` 供业务查重。

接入信息全部来自 ``.env``：``S3_ENDPOINT`` / ``S3_ACCESS_KEY`` / ``S3_SECRET_KEY`` / ``S3_BUCKET``。
本地开发用 ``deploy/docker-compose.rag.yml`` 里的 MinIO（127.0.0.1:9000）。
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

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]+")


@dataclass(frozen=True)
class StoredFile:
    """入桶结果：写进 file_asset 的几个字段。"""

    bucket: str
    object_key: str
    size_bytes: int
    sha256: str


def _safe_name(filename: str) -> str:
    """去掉路径分隔符与特殊字符，避免目录穿越和奇怪文件名。"""
    name = Path(filename).name.strip() or "unnamed"
    cleaned = _SAFE_NAME_RE.sub("_", name)
    return cleaned[:120] or "unnamed"


def clean_upload_name(raw: str | None, *, fallback: str = "unnamed") -> str:
    """规整上传文件名。

    有些客户端（PowerShell 的 multipart、部分老 SDK）会把非 ASCII 文件名按 RFC 2047
    编码，服务端拿到的是 ``=?utf-8?B?...?=`` 这种转义串，扩展名也就丢了。这里先解码回
    可读文件名（真认不出来的由解析层按文件头嗅探兜底），再去掉路径部分防穿越。
    """
    name = (raw or "").strip()
    if not name:
        return fallback
    if name.startswith("=?") and "?" in name[2:]:
        try:
            from email.header import decode_header

            parts = decode_header(name)
            decoded = "".join(
                chunk.decode(encoding or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                for chunk, encoding in parts
            )
            name = decoded.strip() or name
        except Exception:  # 解不出来就用原样
            pass
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or fallback


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
    """改完配置（比如测试里换桶）后清掉客户端缓存。"""
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
    """把内容写进对象存储，返回 bucket / object_key / 大小 / 摘要。

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
    bucket = ensure_bucket()
    _s3_client().put_object(Bucket=bucket, Key=object_key, Body=content)
    return StoredFile(
        bucket=bucket,
        object_key=object_key,
        size_bytes=len(content),
        sha256=digest,
    )


# -------------------------------------------------------------------- 读取


def read_bytes(bucket: str, object_key: str) -> bytes:
    """读对象内容；读不到（不存在 / 无权限）抛 ``NotFoundError``。"""
    try:
        result = _s3_client().get_object(Bucket=bucket, Key=object_key)
    except Exception as exc:
        raise NotFoundError(f"对象不存在：{bucket}/{object_key}") from exc
    return result["Body"].read()


def exists(bucket: str, object_key: str) -> bool:
    try:
        _s3_client().head_object(Bucket=bucket, Key=object_key)
        return True
    except Exception:
        return False


# -------------------------------------------------------------------- 删除


def delete(bucket: str, object_key: str) -> None:
    """删除对象；对象不在（比如已被清理）时静默跳过。"""
    try:
        _s3_client().delete_object(Bucket=bucket, Key=object_key)
    except Exception:
        return


def move_object(bucket: str, source_key: str, target_key: str) -> None:
    """在同一个桶里移动对象（重新组织目录用）：对象存储用 copy + delete。"""
    if source_key == target_key:
        return
    client = _s3_client()
    client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": source_key}, Key=target_key)
    client.delete_object(Bucket=bucket, Key=source_key)


__all__ = [
    "StoredFile",
    "clean_upload_name",
    "delete",
    "ensure_bucket",
    "exists",
    "move_object",
    "project_scope",
    "read_bytes",
    "reset_client_cache",
    "save_bytes",
]
