"""Prompt 指纹（§20.1 / §20.3，P1-13）。

这些断言的价值不在算法本身（sha256 前 16 位没什么可测的），而在两条约束：
指纹里不能出现 prompt 全文，以及同一 Agent 的指纹必须跨会话稳定——后者正是
§9.8 prompt 缓存能不能命中的先决条件。
"""

from __future__ import annotations

from pydantic import SecretStr

from agent_service.agents.research_manager import build_research_manager
from agent_service.config import ExecutionLimits, ProviderCredentials, Settings
from agent_service.models.registry import ModelRegistry
from agent_service.observability.prompts import fingerprint


def _planner_prompt(max_tasks: int = 6) -> tuple[str, int]:
    registry = ModelRegistry(
        Settings(providers=ProviderCredentials(deepseek_api_key=SecretStr("sk-test")))
    )
    built = build_research_manager(registry, ExecutionLimits(max_tasks_per_plan=max_tasks))
    return built.prompt.hash, built.prompt.chars


def test_same_text_gives_the_same_hash() -> None:
    assert fingerprint("你是研究规划器").hash == fingerprint("你是研究规划器").hash


def test_one_character_change_changes_the_hash() -> None:
    assert fingerprint("abc").hash != fingerprint("abd").hash


def test_hash_is_short_enough_to_read_in_a_table() -> None:
    # 用途是比对相等，不是密码学承诺；64 个 hex 字符会让日志全是噪音
    assert len(fingerprint("x").hash) == 16


def test_chars_counts_characters_not_bytes() -> None:
    # 中文 prompt 下按字节算会虚高 3 倍，"prompt 是不是变长了"就没法判断
    assert fingerprint("你好").chars == 2


def test_fingerprint_does_not_embed_the_prompt() -> None:
    """§20.3：埋点里禁止出现 prompt 全文。"""
    prompt = "内部研究方法论：先看协议收入再看解锁"
    digest = fingerprint(prompt)

    assert prompt not in digest.hash
    assert prompt not in str(digest)


def test_planner_prompt_hash_is_stable_across_builds() -> None:
    """同一配置构造两次，指纹必须一致。

    不一致就说明 prompt 前缀里混进了时间戳或随机内容，而这会让缓存命中率
    从 90%+ 掉到 0，且不报任何错——只体现在账单上（§9.8）。
    """
    assert _planner_prompt()[0] == _planner_prompt()[0]


def test_planner_prompt_hash_tracks_the_task_limit() -> None:
    """`max_tasks` 是渲染进 prompt 的，改它就该换一份缓存前缀。"""
    assert _planner_prompt(max_tasks=6)[0] != _planner_prompt(max_tasks=10)[0]


def test_planner_prompt_is_long_enough_for_caching() -> None:
    """缓存有最小长度门槛（Kimi 为 256 token，其余各家类似，§9.8）。

    按中文约 1 字符/token 保守估算，prompt 明显短于这个量级时缓存根本不会生效，
    §9.8 的整套编码约束也就失去意义。
    """
    assert _planner_prompt()[1] > 1000
