"""P1-4b 结构化输出三路径测试。

三条路径各有覆盖，重点是 json_mode——它是开发期（DeepSeek / 智谱）的正常路径。
"""

from __future__ import annotations

import json

import pytest
from agents import Agent, AgentOutputSchema, ModelSettings
from pydantic import BaseModel, Field

from agent_service.models.capabilities import (
    ModelCapabilities,
    StructuredOutputMode,
)
from agent_service.models.catalog import get_entry
from agent_service.models.structured_output import (
    StructuredOutputError,
    apply_strategy,
    build_strategy,
    extract_json_object,
    format_validation_feedback,
    inline_refs,
    parse_output,
    render_schema_instructions,
    run_structured,
)
from agent_service.schemas import ResearchPlan
from agent_service.testing import FakeModel


class Sample(BaseModel):
    symbol: str
    price: float
    tags: list[str] = Field(default_factory=list)


def _caps(mode: StructuredOutputMode) -> ModelCapabilities:
    return ModelCapabilities(
        tool_calling=True,
        parallel_tool_calls=True,
        structured_output=mode,
        streaming=True,
        reasoning=False,
        vision=False,
        context_window=100_000,
        max_output_tokens=8_000,
    )


# ─── schema 内联 ─────────────────────────────────────────────────────────────


def test_inline_refs_removes_all_refs() -> None:
    """§9.4 要求 schema 对弱模型保持扁平，不能留跨节引用。"""
    inlined = inline_refs(ResearchPlan.model_json_schema())
    dumped = json.dumps(inlined)

    assert "$ref" not in dumped
    assert "$defs" not in dumped


def test_inline_refs_preserves_nested_field_names() -> None:
    inlined = inline_refs(ResearchPlan.model_json_schema())
    task_schema = inlined["properties"]["tasks"]["items"]
    assert set(task_schema["properties"]) >= {"id", "agent", "objective", "entities"}


def test_inline_refs_keeps_reference_site_description() -> None:
    """引用处的 description 通常比定义处更贴合上下文，必须保留。"""
    schema = {
        "properties": {"a": {"$ref": "#/$defs/X", "description": "调用点说明"}},
        "$defs": {"X": {"type": "object", "description": "定义处说明"}},
    }
    assert inline_refs(schema)["properties"]["a"]["description"] == "调用点说明"


def test_inline_refs_survives_self_reference() -> None:
    """自引用类型不能导致无限递归。"""
    schema = {
        "properties": {"child": {"$ref": "#/$defs/Node"}},
        "$defs": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}}},
    }
    assert json.dumps(inline_refs(schema))  # 不抛异常、不超时即通过


def test_rendered_instructions_mention_json() -> None:
    """json_object 模式要求 prompt 中出现 JSON 字样，否则多数实现直接报错。"""
    assert "JSON" in render_schema_instructions(Sample)


def test_rendered_instructions_are_deterministic() -> None:
    """prompt 前缀必须逐字节稳定，否则缓存永不命中，成本差一个数量级（§9.8）。"""
    assert render_schema_instructions(Sample) == render_schema_instructions(Sample)


# ─── 文本解析 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        '{"symbol": "NVDA", "price": 1.5, "tags": []}',
        '```json\n{"symbol": "NVDA", "price": 1.5, "tags": []}\n```',
        '```\n{"symbol": "NVDA", "price": 1.5, "tags": []}\n```',
        '好的，结果如下：\n{"symbol": "NVDA", "price": 1.5, "tags": []}',
        '{"symbol": "NVDA", "price": 1.5, "tags": []}\n\n希望对你有帮助！',
    ],
)
def test_parses_json_despite_common_model_habits(raw: str) -> None:
    """即使明确要求不要包代码块/不要加解释，模型仍然经常这么干。"""
    parsed = parse_output(raw, Sample)
    assert parsed.symbol == "NVDA"


def test_extract_handles_braces_inside_strings() -> None:
    """字符串里的花括号不能干扰配平计数。"""
    raw = '前言 {"symbol": "A{B}C", "price": 1.0, "tags": []} 后记'
    assert parse_output(raw, Sample).symbol == "A{B}C"


def test_extract_handles_escaped_quotes() -> None:
    raw = r'{"symbol": "say \"hi\"", "price": 1.0, "tags": []}'
    assert parse_output(raw, Sample).symbol == 'say "hi"'


def test_extract_handles_nested_objects() -> None:
    raw = '说明\n{"symbol": "X", "price": 1.0, "tags": [], "extra": {"a": {"b": 1}}}\n完'
    assert extract_json_object(raw).endswith("}")
    assert parse_output(raw, Sample).symbol == "X"


# ─── 错误反馈 ────────────────────────────────────────────────────────────────


def test_feedback_names_the_offending_field() -> None:
    """回喂具体字段错误，而不是让模型碰运气重跑。"""
    with pytest.raises(Exception) as excinfo:
        Sample.model_validate({"symbol": "X"})

    feedback = format_validation_feedback(excinfo.value)
    assert "price" in feedback
    assert "重新输出" in feedback


def test_feedback_contains_no_stack_trace() -> None:
    """同 §8.1 对 tool 错误的原则：模型看到栈信息只是浪费 token。"""
    feedback = format_validation_feedback(ValueError("bad json at line 3"))
    for noise in ("Traceback", ".py", 'File "'):
        assert noise not in feedback


# ─── 策略选择 ────────────────────────────────────────────────────────────────


def test_native_schema_delegates_to_sdk() -> None:
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.NATIVE_SCHEMA))

    assert isinstance(strategy.agent_output_type, AgentOutputSchema)
    assert strategy.needs_manual_parsing is False
    assert strategy.instructions_suffix == ""
    assert strategy.extra_body == {}


def test_json_mode_must_not_set_output_type() -> None:
    """关键约束：一旦设置 output_type，SDK 就会强行发送 json_schema 格式的
    response_format，而 json_mode 模型只认 json_object。"""
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE))

    assert strategy.agent_output_type is None
    assert strategy.needs_manual_parsing is True
    assert strategy.extra_body == {"response_format": {"type": "json_object"}}
    assert "JSON Schema" in strategy.instructions_suffix


def test_prompt_only_has_no_response_format() -> None:
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.PROMPT_ONLY))

    assert strategy.agent_output_type is None
    assert strategy.extra_body == {}
    assert strategy.instructions_suffix != ""


def test_deepseek_resolves_to_json_mode() -> None:
    """开发期主力走的是哪条路径，直接决定要不要认真对待重试机制。"""
    entry = get_entry("deepseek:deepseek-v4-pro")
    assert build_strategy(Sample, entry.capabilities).mode is StructuredOutputMode.JSON_MODE


# ─── 应用到 Agent ────────────────────────────────────────────────────────────


def test_apply_appends_schema_to_instructions_end() -> None:
    """必须追加在末尾：插在开头会破坏 prompt 缓存前缀（§9.8）。"""
    agent = Agent(name="t", instructions="你是研究助手。", model=FakeModel())
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE))

    prepared = apply_strategy(agent, strategy)

    assert isinstance(prepared.instructions, str)
    assert prepared.instructions.startswith("你是研究助手。")
    assert prepared.instructions.endswith("```")


def test_apply_does_not_mutate_original_agent() -> None:
    agent = Agent(name="t", instructions="原始", model=FakeModel())
    apply_strategy(agent, build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE)))
    assert agent.instructions == "原始"


def test_apply_merges_extra_body_without_dropping_existing() -> None:
    agent = Agent(
        name="t",
        instructions="x",
        model=FakeModel(),
        model_settings=ModelSettings(extra_body={"custom": 1}),
    )
    prepared = apply_strategy(agent, build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE)))

    assert prepared.model_settings.extra_body == {
        "custom": 1,
        "response_format": {"type": "json_object"},
    }


def test_apply_rejects_callable_instructions_in_json_mode() -> None:
    """动态 instructions 无法在编译期追加 schema，也会破坏缓存前缀。"""
    agent = Agent(name="t", instructions=lambda _ctx, _agent: "动态", model=FakeModel())
    with pytest.raises(TypeError, match="静态字符串"):
        apply_strategy(agent, build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE)))


# ─── 重试（用 FakeModel 驱动）─────────────────────────────────────────────────


@pytest.fixture
def json_mode_agent() -> Agent[None]:
    return Agent(name="tester", instructions="你是测试助手。", model=FakeModel())


async def test_valid_output_needs_one_attempt(json_mode_agent: Agent[None]) -> None:
    json_mode_agent.model = FakeModel(['{"symbol": "HYPE", "price": 42.0, "tags": ["defi"]}'])
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE))

    outcome = await run_structured(json_mode_agent, "查 HYPE", strategy=strategy)

    assert outcome.output.symbol == "HYPE"
    assert outcome.output.tags == ["defi"]
    assert outcome.attempts == 1


async def test_malformed_json_is_repaired_on_retry(json_mode_agent: Agent[None]) -> None:
    """P1-4b 的核心验收：故意返回坏 JSON 能被修正。"""
    model = FakeModel(
        [
            "这不是 JSON，只是一段闲聊。",
            '{"symbol": "HYPE", "price": 42.0, "tags": []}',
        ]
    )
    json_mode_agent.model = model
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE))

    outcome = await run_structured(json_mode_agent, "查 HYPE", strategy=strategy)

    assert outcome.output.symbol == "HYPE"
    assert outcome.attempts == 2


async def test_missing_field_is_repaired_on_retry(json_mode_agent: Agent[None]) -> None:
    model = FakeModel(
        [
            '{"symbol": "HYPE"}',  # 缺 price
            '{"symbol": "HYPE", "price": 42.0, "tags": []}',
        ]
    )
    json_mode_agent.model = model
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE))

    outcome = await run_structured(json_mode_agent, "查 HYPE", strategy=strategy)
    assert outcome.attempts == 2


async def test_retry_feeds_the_error_back_to_the_model(
    json_mode_agent: Agent[None],
) -> None:
    """重试必须带上具体错误；不带就只是碰运气。"""
    model = FakeModel(['{"symbol": "HYPE"}', '{"symbol": "HYPE", "price": 1.0, "tags": []}'])
    json_mode_agent.model = model

    await run_structured(
        json_mode_agent,
        "查 HYPE",
        strategy=build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE)),
    )

    second_input = model.calls[1].input
    assert isinstance(second_input, list)
    assert "price" in json.dumps(second_input, ensure_ascii=False)


async def test_gives_up_after_max_retries(json_mode_agent: Agent[None]) -> None:
    json_mode_agent.model = FakeModel(["永远不是 JSON"])
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE))

    with pytest.raises(StructuredOutputError) as excinfo:
        await run_structured(json_mode_agent, "查 HYPE", strategy=strategy, max_retries=2)

    assert excinfo.value.attempts == 3
    assert excinfo.value.model_name == "Sample"


async def test_native_schema_path_skips_manual_parsing(
    json_mode_agent: Agent[None],
) -> None:
    """native_schema 下 SDK 已校验过，我们不应再解析一遍文本。"""
    json_mode_agent.model = FakeModel(['{"symbol": "NVDA", "price": 9.9, "tags": []}'])
    strategy = build_strategy(Sample, _caps(StructuredOutputMode.NATIVE_SCHEMA))

    outcome = await run_structured(json_mode_agent, "查 NVDA", strategy=strategy)

    assert outcome.output.price == pytest.approx(9.9)
    assert outcome.attempts == 1


async def test_json_mode_sends_json_object_response_format(
    json_mode_agent: Agent[None],
) -> None:
    """端到端确认 response_format 真的传到了模型层。"""
    model = FakeModel(['{"symbol": "X", "price": 1.0, "tags": []}'])
    json_mode_agent.model = model

    await run_structured(
        json_mode_agent,
        "查 X",
        strategy=build_strategy(Sample, _caps(StructuredOutputMode.JSON_MODE)),
    )

    extra_body = model.last_call.model_settings.extra_body
    assert extra_body == {"response_format": {"type": "json_object"}}
    assert model.last_call.output_schema_name is None
