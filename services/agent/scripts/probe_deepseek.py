"""用真实 DeepSeek 验证 P1-4b 的 json_mode 路径与 planner 可行性。

刻意不做成 pytest：它会真实计费、结果不确定，不能进 CI。
需要时手动跑：`uv run python scripts/probe_deepseek.py`
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from agents import Agent

from agent_service.models.registry import ModelRegistry, bootstrap_sdk
from agent_service.models.structured_output import build_strategy, run_structured
from agent_service.schemas import ModelRole, ResearchPlan

PLANNER_PROMPT = """你是金融研究的规划者。把用户问题拆解为可并行执行的研究任务。

可用的执行 agent：
- crypto_research：链上数据、代币经济、协议指标
- stock_research：财报、估值、SEC 文件
- web_research：新闻、公告、第三方分析

规则：
- 任务数不超过 6 个
- 每个任务的 objective 必须具体到可验证
- depends_on 只能引用同一 plan 内已出现的任务 id，且不得成环
"""

QUESTIONS = [
    "Hyperliquid 的收入模型能支撑当前估值吗？",
    "英伟达最新财报里数据中心业务的增速是否在放缓？",
    "比较 Solana 和 Ethereum 当前的质押收益率与通胀率。",
]


async def main() -> None:
    bootstrap_sdk()  # 无 OpenAI key 时关闭 tracing，否则每轮都刷一行导出失败
    registry = ModelRegistry()
    resolved = registry.for_role(ModelRole.PLANNER)
    entry = resolved.entry
    pricing = entry.capabilities.pricing
    strategy = build_strategy(ResearchPlan, entry.capabilities)
    now = datetime.now(UTC)

    print(f"模型     : {entry.id} ({entry.display_name})")
    print(f"路径     : {strategy.mode.value}")
    print(f"schema   : {len(strategy.instructions_suffix)} 字符内嵌到 instructions 末尾")
    if pricing.schedule is not None:
        band = "高峰" if pricing.schedule.is_peak(now) else "闲时"
        prices = pricing.prices_at(now)
        print(f"计费时段 : {band}（输入 ${prices.input}/M，输出 ${prices.output}/M）")
    print("=" * 74)

    agent = Agent(
        name="research_manager",
        instructions=PLANNER_PROMPT,
        model=resolved.model,
        model_settings=resolved.settings,
    )
    total_cost = 0.0

    for question in QUESTIONS:
        print(f"\n▸ {question}")
        started = time.monotonic()
        try:
            outcome = await run_structured(agent, question, strategy=strategy)
        except Exception as error:
            print(f"  ❌ {type(error).__name__}: {error}")
            continue

        elapsed = time.monotonic() - started
        plan = outcome.output
        usage = outcome.result.context_wrapper.usage
        cached = _cached_tokens(usage)
        cost = pricing.cost_usd(
            input_tokens=usage.input_tokens - cached,
            output_tokens=usage.output_tokens,
            cached_tokens=cached,
            at=now,
        )
        total_cost += cost

        print(
            f"  ✅ {elapsed:.1f}s / {outcome.attempts} 次调用 / ${cost:.4f}"
            f" / in={usage.input_tokens}(缓存{cached}) out={usage.output_tokens}"
        )
        print(f"     类型={plan.question_type.value}  任务数={len(plan.tasks)}")
        print(f"     解读：{plan.interpretation[:70]}")
        for task in plan.tasks:
            deps = f"  ←{task.depends_on}" if task.depends_on else ""
            print(f"       [{task.id}] {task.agent.value}: {task.objective[:50]}{deps}")

        _check_plan_integrity(plan)

    print("\n" + "=" * 74)
    print(f"总成本 ${total_cost:.4f}（{len(QUESTIONS)} 个问题）")


def _cached_tokens(usage: object) -> int:
    """从 SDK 的 Usage 里取缓存命中量。

    缓存与未命中的价差可达 30 倍（§9.8），必须分开计价；但这个字段
    并非所有 provider 都返回，取不到时按 0 算（宁可高估成本）。
    """
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0)
    return cached if isinstance(cached, int) else 0


def _check_plan_integrity(plan: ResearchPlan) -> None:
    """schema 合法不等于计划合法——依赖引用与环需要代码校验（P1-9 会正式实现）。"""
    ids = [task.id for task in plan.tasks]
    problems: list[str] = []

    if len(ids) != len(set(ids)):
        problems.append("任务 id 重复")
    for task in plan.tasks:
        unknown = [dep for dep in task.depends_on if dep not in ids]
        if unknown:
            problems.append(f"{task.id} 依赖不存在的任务 {unknown}")
        if task.id in task.depends_on:
            problems.append(f"{task.id} 依赖自己")

    print(f"     {'⚠️  ' + '; '.join(problems) if problems else '依赖校验通过'}")


if __name__ == "__main__":
    asyncio.run(main())
