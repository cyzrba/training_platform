"""约束命名规范：未显式命名的约束按此规则生成，保证迁移脚本稳定。"""

from typing import Final

# 显式命名（uk_* / idx_* / chk_*）的约束在模型里直接带 name，这里只做兜底
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "uq": "uk_%(table_name)s_%(column_0_N_name)s",
    "ck": "%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}
