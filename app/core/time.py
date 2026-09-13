"""时间工具：项目单时区部署，统一取本地时间。"""

from datetime import datetime


def now() -> datetime:
    """当前本地时间（不带时区标记），用作 created_at / updated_at 的默认值。

    本项目固定在同一时区运行（单学院、单机房），不做跨时区换算，
    因此直接落本地时间即可，读写语义天然一致。
    若将来需要多时区部署，只改这一处即可。
    """
    return datetime.now()
