"""Prompt 模板加载。

prompt 放在 `.md` 文件而非 Python 字符串里，理由有三：改 prompt 的 diff 干净
可 review；不必担心 Python 转义与缩进；以后做 prompt 版本对比（Phase 7 eval）
时可以直接按文件哈希归档。

**加载结果被缓存，因为 §9.8 要求 system prompt 逐字节稳定。** 缓存命中价仅为
未命中的 3%，一次意外的字符串变动（哪怕只多一个空格）就会让整个会话的缓存全部
失效。因此模板里严禁出现时间戳、session id 之类易变内容——那些必须放在 user
消息里。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")


class PromptNotFoundError(FileNotFoundError):
    def __init__(self, name: str) -> None:
        available = sorted(path.stem for path in _PROMPT_DIR.glob("*.md"))
        super().__init__(f"找不到 prompt {name!r}。可用的有：{', '.join(available)}")
        self.name = name


class PromptPlaceholderError(ValueError):
    """模板里有占位符没被替换，或传了模板里不存在的变量。"""


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """读取原始模板。结果缓存，保证同一进程内逐字节一致。"""
    path = _PROMPT_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise PromptNotFoundError(name) from None


def render_prompt(name: str, **variables: object) -> str:
    """替换 `{{UPPER_SNAKE}}` 占位符。

    刻意不用 `str.format`：模板里有 JSON 示例，花括号会被 format 误解析。

    未替换的占位符会**报错而非放行**——把 `{{MAX_TASKS}}` 原样发给模型是那种
    不会崩、只会让输出悄悄变差的故障，等到 eval 阶段才发现代价太高。
    """
    template = load_prompt(name)
    expected = set(_PLACEHOLDER_RE.findall(template))
    provided = {key.upper() for key in variables}

    if unknown := provided - expected:
        msg = (
            f"prompt {name!r} 里没有这些占位符：{sorted(unknown)}；模板需要的是 {sorted(expected)}"
        )
        raise PromptPlaceholderError(msg)
    if missing := expected - provided:
        msg = f"prompt {name!r} 缺少占位符取值：{sorted(missing)}"
        raise PromptPlaceholderError(msg)

    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key.upper()}}}}}", str(value))
    return rendered
