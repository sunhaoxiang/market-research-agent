"""P1-3 模型目录测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from agent_service.models import CATALOG, ProviderId, get_entry, list_entries
from agent_service.models.capabilities import (
    PeakSchedule,
    Pricing,
    StructuredOutputMode,
    TokenPrices,
)
from agent_service.models.catalog import ROLE_DEFAULTS, UnknownModelError
from agent_service.schemas.common import ModelRole

BEIJING = ZoneInfo("Asia/Shanghai")


# ─── 目录完整性 ──────────────────────────────────────────────────────────────


def test_model_ids_are_unique() -> None:
    ids = [entry.id for entry in CATALOG]
    assert len(ids) == len(set(ids))


def test_model_id_matches_provider_prefix() -> None:
    """ID 形如 `provider:model`，前缀必须与 provider 字段一致，否则 registry 会挑错客户端。"""
    for entry in CATALOG:
        prefix, _, rest = entry.id.partition(":")
        assert prefix == entry.provider.value, f"{entry.id} 的前缀与 provider 不符"
        assert rest, f"{entry.id} 缺少模型名部分"


def test_role_defaults_point_to_existing_entries() -> None:
    for role, model_id in ROLE_DEFAULTS.items():
        assert get_entry(model_id) is not None, f"{role} 的默认模型 {model_id} 不在目录中"


def test_all_roles_have_a_default() -> None:
    assert set(ROLE_DEFAULTS) == set(ModelRole)


def test_dev_defaults_are_deepseek() -> None:
    """开发期默认必须是国内模型；改回 OpenAI 时这个断言会提醒同步更新 .env.example。"""
    for model_id in ROLE_DEFAULTS.values():
        assert model_id.startswith("deepseek:")
    assert set(ROLE_DEFAULTS.values()) == {"deepseek:deepseek-flash"}


def test_unknown_model_error_lists_alternatives() -> None:
    with pytest.raises(UnknownModelError) as excinfo:
        get_entry("openai:gpt-4")
    assert "deepseek:deepseek-v4-pro" in str(excinfo.value)


def test_list_entries_filters_by_provider() -> None:
    deepseek = list_entries(ProviderId.DEEPSEEK)
    assert len(deepseek) == 3
    assert all(entry.provider is ProviderId.DEEPSEEK for entry in deepseek)
    assert list_entries() == CATALOG


def test_tool_calling_is_required_for_every_entry() -> None:
    """§9.4：不支持 tool calling 的模型直接不进目录，而不是运行时降级。"""
    assert all(entry.capabilities.tool_calling for entry in CATALOG)


def test_default_models_have_verified_pricing() -> None:
    """兜底默认模型必须有核实过的价格，否则成本护栏是空的。"""
    for model_id in set(ROLE_DEFAULTS.values()):
        entry = get_entry(model_id)
        assert entry.verified_at is not None, f"{model_id} 的参数未核实"
        assert entry.capabilities.pricing is not None


# ─── 分时计价 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("moment", "expect_peak"),
    [
        (datetime(2026, 9, 7, 10, 0, tzinfo=BEIJING), True),  # 周一 10:00 高峰
        (datetime(2026, 9, 7, 15, 0, tzinfo=BEIJING), True),  # 周一 15:00 高峰
        (datetime(2026, 9, 7, 12, 30, tzinfo=BEIJING), False),  # 午休落在两窗之间
        (datetime(2026, 9, 7, 23, 0, tzinfo=BEIJING), False),  # 深夜
        (datetime(2026, 9, 7, 8, 59, tzinfo=BEIJING), False),  # 高峰前一分钟
        (datetime(2026, 9, 7, 18, 0, tzinfo=BEIJING), False),  # 区间右开
        (datetime(2026, 9, 5, 10, 0, tzinfo=BEIJING), False),  # 周六全天闲时
    ],
)
def test_deepseek_peak_windows(moment: datetime, expect_peak: bool) -> None:
    entry = get_entry("deepseek:deepseek-v4-pro")
    assert entry.capabilities.pricing is not None
    schedule = entry.capabilities.pricing.schedule
    assert schedule is not None
    assert schedule.is_peak(moment) is expect_peak


def test_peak_schedule_respects_timezone() -> None:
    """UTC 时间戳必须先换算到北京时间——事件的 ts 都是 UTC。"""
    entry = get_entry("deepseek:deepseek-v4-pro")
    assert entry.capabilities.pricing is not None
    schedule = entry.capabilities.pricing.schedule
    assert schedule is not None

    # UTC 02:00 周一 = 北京 10:00 周一，属于高峰
    assert schedule.is_peak(datetime(2026, 9, 7, 2, 0, tzinfo=UTC)) is True
    # UTC 15:00 周一 = 北京 23:00 周一，属于闲时
    assert schedule.is_peak(datetime(2026, 9, 7, 15, 0, tzinfo=UTC)) is False


def test_off_peak_is_half_of_peak() -> None:
    for model_id in (
        "deepseek:deepseek-flash",
        "deepseek:deepseek-v4-pro",
        "deepseek:deepseek-v4-flash",
    ):
        pricing = get_entry(model_id).capabilities.pricing
        assert pricing is not None
        assert pricing.off_peak is not None
        assert pricing.off_peak.input == pytest.approx(pricing.peak.input / 2)
        assert pricing.off_peak.output == pytest.approx(pricing.peak.output / 2)


def test_pricing_requires_schedule_when_off_peak_given() -> None:
    with pytest.raises(ValueError, match="必须同时提供"):
        Pricing(
            peak=TokenPrices(input=1.0, output=2.0),
            off_peak=TokenPrices(input=0.5, output=1.0),
        )


def test_flat_pricing_ignores_time() -> None:
    pricing = Pricing(peak=TokenPrices(input=3.0, output=15.0))
    at_night = datetime(2026, 9, 7, 23, 0, tzinfo=BEIJING)
    assert pricing.prices_at(at_night) == pricing.peak


# ─── 成本计算 ────────────────────────────────────────────────────────────────


def test_cost_uses_off_peak_prices_at_night() -> None:
    pricing = get_entry("deepseek:deepseek-v4-pro").capabilities.pricing
    assert pricing is not None

    night = datetime(2026, 9, 7, 23, 0, tzinfo=BEIJING)
    day = datetime(2026, 9, 7, 10, 0, tzinfo=BEIJING)

    kwargs = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert pricing.cost_usd(**kwargs, at=night) == pytest.approx(0.66 + 1.98)
    assert pricing.cost_usd(**kwargs, at=day) == pytest.approx(1.32 + 3.96)


def test_cached_tokens_are_priced_separately() -> None:
    """缓存命中价是未命中的 3%，混算会让成本护栏完全失真（§9.8）。"""
    pricing = get_entry("deepseek:deepseek-v4-pro").capabilities.pricing
    assert pricing is not None
    night = datetime(2026, 9, 7, 23, 0, tzinfo=BEIJING)

    all_miss = pricing.cost_usd(input_tokens=1_000_000, output_tokens=0, at=night)
    all_hit = pricing.cost_usd(input_tokens=0, cached_tokens=1_000_000, output_tokens=0, at=night)

    assert all_miss == pytest.approx(0.66)
    assert all_hit == pytest.approx(0.022)
    assert all_hit < all_miss / 20


def test_cost_falls_back_to_input_price_without_cached_price() -> None:
    """没有缓存价时按未命中价算——宁可高估，成本护栏不能被低估拖穿。"""
    pricing = Pricing(peak=TokenPrices(input=2.0, output=12.0))
    cost = pricing.cost_usd(
        input_tokens=0,
        cached_tokens=1_000_000,
        output_tokens=0,
        at=datetime(2026, 9, 7, 23, 0, tzinfo=BEIJING),
    )
    assert cost == pytest.approx(2.0)


def test_realistic_session_cost_matches_plan_estimate() -> None:
    """§9.7 估算：约 9.3 万输入 + 2 万输出，DeepSeek Pro 闲时约 $0.10。"""
    pricing = get_entry("deepseek:deepseek-v4-pro").capabilities.pricing
    assert pricing is not None

    cost = pricing.cost_usd(
        input_tokens=93_000,
        output_tokens=20_000,
        at=datetime(2026, 9, 7, 23, 0, tzinfo=BEIJING),
    )
    assert 0.08 < cost < 0.12


# ─── 能力元数据 ──────────────────────────────────────────────────────────────


def test_only_kimi_and_openai_claim_native_schema() -> None:
    """DeepSeek / 智谱保守标为 json_mode，因此 json_mode 是开发期的正常路径而非异常分支。"""
    native = {
        entry.id
        for entry in CATALOG
        if entry.capabilities.structured_output is StructuredOutputMode.NATIVE_SCHEMA
    }
    assert "moonshot:kimi-k3" in native
    assert "deepseek:deepseek-v4-pro" not in native
    assert "zhipu:glm-5.3-flash" not in native


def test_only_deepseek_is_smoke_verified() -> None:
    """P5-9：本机只有 DeepSeek key，完整研究冒烟只标这两条。"""
    verified = {entry.id for entry in CATALOG if entry.verified}
    assert verified == {
        "deepseek:deepseek-flash",
        "deepseek:deepseek-v4-pro",
        "deepseek:deepseek-v4-flash",
    }


def test_peak_schedule_hour_windows_are_well_formed() -> None:
    schedule = PeakSchedule(timezone="Asia/Shanghai", hour_windows=[(9, 12), (14, 18)])
    for start, end in schedule.hour_windows:
        assert 0 <= start < end <= 24
