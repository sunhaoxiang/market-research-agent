"""Prompt 指纹（§20.1 / §20.3，P1-13）。

`agent_runs` 记录 prompt 的 hash 与长度，**绝不记全文**。这不只是隐私考量：
prompt 里可能带上游数据（Phase 2 起会把工具结果拼进去），全量存库会让
库体积随研究次数线性膨胀，而排查问题真正需要的只是"这两次的 prompt 是否
逐字节相同"。

hash 同时是 §9.8 缓存前缀稳定性的验证手段：同一 Agent 在不同会话里的
`prompt_hash` 应当完全一致，一旦出现漂移就说明有人往 prompt 前缀里插了
时间戳或随机内容，缓存命中率会随之崩掉。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

_HASH_CHARS = 16
"""截断到 16 个 hex 字符（64 bit）。

用途是比对相等，不是密码学承诺。64 bit 在这个量级下碰撞概率可以忽略，
而全长 64 字符会让日志和 `/debug` 表格里全是噪音。
"""


@dataclass(frozen=True)
class PromptFingerprint:
    """一段 prompt 的可记录摘要。"""

    hash: str
    chars: int
    """字符数而非 token 数：token 数要调 tokenizer 且各家不同，
    而这个字段的用途是"prompt 是不是突然变长了"，字符数足够回答。"""


def fingerprint(prompt: str) -> PromptFingerprint:
    """计算 prompt 指纹。"""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return PromptFingerprint(hash=digest[:_HASH_CHARS], chars=len(prompt))
