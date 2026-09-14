"""S3（MinIO）存储后端集成测试。

默认跳过：只有本机能连上 MinIO（默认 127.0.0.1:9000）时才跑。
本地起 MinIO 见 README「文件存储」，起完直接 `uv run pytest tests/test_storage_s3.py` 即可。

存储配置由 conftest 的 ``object_storage`` 夹具统一给定，对象落在测试桶
``training-platform-test``，与开发桶隔离。
"""

import socket

import httpx
import pytest

from app.services import storage

MINIO_HOST = "127.0.0.1"
MINIO_PORT = 9000


def _minio_reachable() -> bool:
    try:
        with socket.create_connection((MINIO_HOST, MINIO_PORT), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _minio_reachable(), reason="本机没有可用的 MinIO（127.0.0.1:9000），跳过 S3 集成测试"
)


@pytest.mark.asyncio
async def test_upload_and_download_through_minio(client: httpx.AsyncClient) -> None:
    """上传走 S3 协议进 MinIO，下载再从 MinIO 取回来。"""
    content = "缺陷检测实训报告模板：一、需求分析…".encode()
    uploaded = (
        await client.post(
            "/api/file-assets/upload",
            files={"file": ("报告模板.md", content, "text/markdown")},
            data={"biz_type": "REPORT_TEMPLATE"},
        )
    ).json()
    assert uploaded["bucket"] == "training-platform-test"
    assert uploaded["size_bytes"] == len(content)
    assert uploaded["object_key"].startswith("misc/report_template/")  # 未绑项目走 misc/

    # 对象确实在 MinIO 里
    client_s3 = storage._s3_client()
    obj = client_s3.get_object(Bucket=uploaded["bucket"], Key=uploaded["object_key"])
    assert obj["Body"].read() == content

    # 通过接口下载回来
    download = await client.raw.get(f"/api/file-assets/{uploaded['id']}/download")
    assert download.status_code == 200
    assert download.content == content

    # 挂到项目上再删关联，对象仍然在 MinIO
    project = (
        await client.post("/api/projects", json={"project_name": "MinIO 附件实训", "project_level": "BASIC"})
    ).json()
    link = (
        await client.post(
            f"/api/projects/{project['id']}/files",
            json={"file_asset_id": uploaded["id"], "file_kind": "REPORT_TEMPLATE"},
        )
    ).json()
    # 绑到项目后，再上传的文件才进项目目录
    in_project = (
        await client.post(
            f"/api/projects/{project['id']}/files/upload",
            files={"file": ("数据说明.md", b"data", "text/markdown")},
            data={"file_kind": "GUIDE"},
        )
    ).json()
    in_project_asset = await _asset_of(client, in_project["file_asset_id"])
    # 项目名里的空格会被安全化处理成下划线（控制台里依然一眼看得出是哪个项目）
    assert in_project_asset["object_key"].startswith(f"projects/{project['id']}-MinIO_附件实训/guide/")
    storage.delete(in_project_asset["bucket"], in_project_asset["object_key"])
    await client.delete(f"/api/projects/{project['id']}/files/{link['id']}")
    assert storage.exists(uploaded["bucket"], uploaded["object_key"])

    # 收尾：清掉测试对象
    storage.delete(uploaded["bucket"], uploaded["object_key"])
    assert not storage.exists(uploaded["bucket"], uploaded["object_key"])


async def _asset_of(client: httpx.AsyncClient, asset_id: int) -> dict:
    """从文件台账里取出某条记录（拿 bucket / object_key）。"""
    listed = (await client.get("/api/file-assets", params={"page_size": 50})).json()
    return next(item for item in listed["items"] if item["id"] == asset_id)
