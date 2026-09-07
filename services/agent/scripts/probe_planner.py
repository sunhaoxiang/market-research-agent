"""P1-9 验收：用真实模型跑 Research Manager，并对比候选模型的延迟与成本。

刻意不做成 pytest：真实计费、结果不确定，不能进 CI。

    uv run python scripts/probe_planner.py                       # 对比全部候选
    uv run python scripts/probe_planner.py deepseek:deepseek-v4-flash

验收标准（DEVELOPMENT_PLAN §7.3 / ROADMAP P1-9）：三个样例问题都能产出
**无需修复即通过语义校验**的计划。`validate_plan` 报出的 issue 越多说明 prompt
越该改，靠校验层兜住只是把问题藏起来。
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agent_service.agents.research_manager import build_research_manager
from agent_service.config import Settings, get_settings
from agent_service.models.registry import ModelRegistry, bootstrap_sdk
from agent_service.orchestrator.plan_validation import ValidatedPlan
from agent_service.orchestrator.planner import create_plan

CANDIDATES = ["deepseek:deepseek-v4-flash", "deepseek:deepseek-v4-pro"]
"""默认对比项。

原本的猜想是「v4-pro 单次 17-60 秒太慢，换 flash」，实测否证了它：
flash 在规划任务上输出 4.3k-4.7k token（pro 只要 2.1k-2.7k），中位延迟
42s 反而略高于 pro 的 38s，且 5 次里 1 次返回空 content。结论是保留 pro。
把两个模型都留在这里，是为了换 prompt 或换模型版本后能重跑这个对比。"""

QUESTIONS = [
    "Hyperliquid 的收入模型能支撑当前估值吗？",
    "英伟达最新财报里数据中心业务的增速是否在放缓？",
    "比较 Solana 和 Ethereum 当前的质押收益率与通胀率。",
]


@dataclass(frozen=True)
class Outcome:
    elapsed_s: float
    attempts: int = 0
    cost_usd: float = 0.0
    validated: ValidatedPlan | None = None
    error: str | None = None

    @property
    def repaired(self) -> bool:
        return bool(self.validated and self.validated.issues)


@dataclass
class ModelReport:
    model_id: str
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def succeeded(self) -> list[Outcome]:
        return [outcome for outcome in self.outcomes if outcome.validated is not None]


async def main() -> None:
    bootstrap_sdk()  # 无 OpenAI key 时关闭 tracing，否则每轮都刷一行导出失败
    model_ids = sys.argv[1:] or CANDIDATES
    settings = get_settings()
    now = datetime.now(UTC)

    reports = [await _probe(model_id, settings, now) for model_id in model_ids]
    _summarize(reports)


async def _probe(model_id: str, settings: Settings, now: datetime) -> ModelReport:
    print(f"\n{'━' * 76}\n模型：{model_id}\n{'━' * 76}")

    # 用 role 覆盖切模型，走的正是线上「改环境变量换模型」那条路径（§9.5）
    registry = ModelRegistry(settings.model_copy(update={"model_role_planner": model_id}))
    limits = settings.limits
    planner = build_research_manager(registry, limits)
    pricing = registry.resolve(model_id).entry.capabilities.pricing

    if pricing.schedule is not None:
        band = "高峰" if pricing.schedule.is_peak(now) else "闲时"
        prices = pricing.prices_at(now)
        print(f"计费时段：{band}（输入 ${prices.input}/M，输出 ${prices.output}/M）")

    report = ModelReport(model_id)
    for question in QUESTIONS:
        print(f"\n▸ {question}")
        started = time.monotonic()
        try:
            result = await create_plan(question, planner=planner, limits=limits, now=now)
        # 探针不该因单个问题中断：一个模型拒绝 json_object 不代表另一个也会
        except Exception as error:
            elapsed = time.monotonic() - started
            print(f"  ❌ {elapsed:.1f}s  {type(error).__name__}: {error}")
            report.outcomes.append(Outcome(elapsed, error=type(error).__name__))
            continue

        elapsed = time.monotonic() - started
        usage = result.run_result.context_wrapper.usage
        cached = _cached_tokens(usage)
        cost = pricing.cost_usd(
            input_tokens=usage.input_tokens - cached,
            output_tokens=usage.output_tokens,
            cached_tokens=cached,
            at=now,
        )
        report.outcomes.append(Outcome(elapsed, result.attempts, cost, validated=result.validated))

        print(
            f"  ✅ {elapsed:.1f}s / {result.attempts} 次调用 / ${cost:.4f}"
            f" / in={usage.input_tokens}(缓存{cached}) out={usage.output_tokens}"
        )
        _print_plan(result.validated)

    return report


def _print_plan(validated: ValidatedPlan) -> None:
    plan = validated.plan
    print(f"     类型={plan.question_type.value}  章节={plan.report_sections}")
    print(f"     解读：{plan.interpretation[:80]}")
    for index, layer in enumerate(validated.layers, start=1):
        for task in layer:
            deps = f"  ←{task.depends_on}" if task.depends_on else ""
            objective = task.objective[:56]
            print(f"       L{index} [{task.id}] {task.agent.value}: {objective}{deps}")
    if plan.assumptions:
        print(f"     假设：{'; '.join(plan.assumptions)[:100]}")
    if validated.issues:
        print(f"     ⚠️  被修复：{'; '.join(issue.detail for issue in validated.issues)}")
    else:
        print("     语义校验通过，无需修复")


def _summarize(reports: list[ModelReport]) -> None:
    print(f"\n{'━' * 76}\n对比\n{'━' * 76}")
    print(f"{'模型':<32}{'成功':>6}{'中位延迟':>11}{'最慢':>9}{'均成本':>10}{'被修复':>8}")

    for report in reports:
        ok = report.succeeded
        latencies = [outcome.elapsed_s for outcome in ok]
        median = f"{statistics.median(latencies):.1f}s" if latencies else "—"
        slowest = f"{max(latencies):.1f}s" if latencies else "—"
        cost = f"${statistics.mean([o.cost_usd for o in ok]):.4f}" if ok else "—"
        repaired = sum(1 for outcome in ok if outcome.repaired)
        print(
            f"{report.model_id:<32}{f'{len(ok)}/{len(report.outcomes)}':>6}"
            f"{median:>11}{slowest:>9}{cost:>10}{repaired:>8}"
        )

    print(
        "\n判读：「被修复」非 0 说明 prompt 该改；延迟要连输出 token 一起看——"
        "推理模型的延迟由输出量决定，标称更快的档位不一定真的更快。"
    )


def _cached_tokens(usage: object) -> int:
    """从 SDK 的 Usage 里取缓存命中量。

    缓存与未命中的价差可达 30 倍（§9.8），必须分开计价；但这个字段
    并非所有 provider 都返回，取不到时按 0 算（宁可高估成本）。
    """
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0)
    return cached if isinstance(cached, int) else 0


if __name__ == "__main__":
    asyncio.run(main())
