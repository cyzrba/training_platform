"""密码策略：只存哈希，校验时用固定时间比较。

存储格式：``pbkdf2_sha256$<迭代次数>$<盐>$<摘要>``，算法用标准库，不引入额外依赖。
"""

import hashlib
import hmac
import secrets

from app.core.config import settings

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 260_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """把明文密码转成可落库的哈希串（每次调用盐都不同）。"""
    salt = secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    """校验明文密码与哈希串是否匹配；未设置密码（None/空）一律不通过。"""
    if not encoded:
        return False
    try:
        algorithm, iterations, salt, digest = encoded.split("$", 3)
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    if algorithm != ALGORITHM:
        return False
    return hmac.compare_digest(expected.hex(), digest)


def default_password_hash() -> str:
    """新建账号（含 Excel 导入的学生）时使用的默认密码哈希。"""
    return hash_password(settings.default_password)
