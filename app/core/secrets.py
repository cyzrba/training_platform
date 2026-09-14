"""配置里的敏感字段掩码。

``system_config`` 存着模型 api key 这类凭据，而 ``/api/system-configs`` 是通用配置接口，
如果原样返回就等于把 key 摆在接口上了。掩码统一在**出参序列化**这一层做：只要返回值走
``SystemConfigRead``，无论哪个接口都自动脱敏，不依赖每个接口自己记得。

注意：掩码只挡接口出口。真正取明文只有服务端内部走
``settings_store.get_llm_config()``——那里读的是仓储返回的原始 dict，不经过这里。
"""

import re
from typing import Any

#: 命中即视为密钥的键名（大小写不敏感，允许 api_key / apiKey / api-key）
SECRET_KEY_RE = re.compile(r"api[_-]?key|secret|token|password|passwd|pwd|credential", re.I)

#: 掩码保留的尾部字符数：够人核对"是不是换过 key"，又不足以还原
VISIBLE_TAIL = 4


def is_secret_key(key: str) -> bool:
    return bool(SECRET_KEY_RE.search(key))


def mask_value(value: str) -> str:
    """掩码单个值：留尾 4 位。空值保持空——空表示"还没配"，不该假装成已配置。"""
    if not value:
        return ""
    if len(value) <= VISIBLE_TAIL:
        return "*" * len(value)
    return f"{'*' * 4}{value[-VISIBLE_TAIL:]}"


def mask_secrets(payload: Any) -> Any:
    """递归掩码：dict 按 key 判断，list 逐项下探，其它类型原样返回。"""
    if isinstance(payload, dict):
        masked: dict[Any, Any] = {}
        for key, value in payload.items():
            if is_secret_key(str(key)) and isinstance(value, str):
                masked[key] = mask_value(value)
            else:
                masked[key] = mask_secrets(value)
        return masked
    if isinstance(payload, list):
        return [mask_secrets(item) for item in payload]
    return payload


__all__ = ["SECRET_KEY_RE", "is_secret_key", "mask_secrets", "mask_value"]
