"""结构化输出的三条路径（§9.4 / R3）。

这是多模型支持真正的难点。为什么必须自己做而不能全交给 SDK：
`chatcmpl_converter.convert_response_format()` 在有 `output_type` 时**永远**发送
`response_format={"type":"json_schema", ...}`，`is_strict_json_schema()` 只能切换其中的
`strict` 标志。而 DeepSeek / 智谱这类模型只支持 `{"type":"json_object"}`——
它们是开发期的主力，所以这条路径是**正常路径而非异常分支**。

三条路径：

    native_schema  交给 SDK 的 output_type，模型侧保证字段级约束
    json_mode      Agent 返回纯文本 + response_format=json_object
                   + prompt 内嵌 schema + 代码侧校验重试
    prompt_only    同上但没有 response_format 兜底，只靠 prompt 约束

后两条共用同一套「渲染 schema → 解析文本 → 带错误重试」的机制，
区别只在要不要给模型加 `json_object` 这道保险。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from agents import Agent, AgentOutputSchema, AgentOutputSchemaBase, ModelSettings, Runner
from pydantic import BaseModel, ValidationError

from agent_service.models.capabilities import ModelCapabilities, StructuredOutputMode

if TYPE_CHECKING:
    from agents.items import TResponseInputItem
    from agents.result import RunResult

log = structlog.get_logger(__name__)

_MAX_INLINE_DEPTH = 6
"""schema 内联的深度上限。超过则保留 $ref，避免自引用类型把 prompt 撑爆。"""

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class StructuredOutputError(ValueError):
    """模型输出无法解析成目标 schema，且重试已用尽。"""

    def __init__(self, model_name: str, attempts: int, last_error: str, raw: str) -> None:
        super().__init__(
            f"{attempts} 次尝试后仍无法把模型输出解析为 {model_name}。最后一次错误：{last_error}"
        )
        self.model_name = model_name
        self.attempts = attempts
        self.last_error = last_error
        self.raw = raw


# ─────────────────────────────────────────────────────────────────────────────
# schema 渲染
# ─────────────────────────────────────────────────────────────────────────────


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """把 `$defs` 里的定义就地展开，返回自包含的 schema。

    §9.4 要求 schema 对弱模型保持扁平。`{"$ref": "#/$defs/Entity"}` 虽然在
    `$defs` 里有定义，但要求模型自己做跨节引用解析——对 json_mode 这类
    只靠 prompt 约束的路径，展开后的可读性明显更好。

    自引用类型会在深度超限时保留 `$ref` 并附带说明，不会无限递归。
    """
    defs: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any, depth: int) -> Any:
        if isinstance(node, list):
            return [resolve(item, depth) for item in node]
        if not isinstance(node, dict):
            return node

        typed_node: dict[str, Any] = node
        ref = typed_node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            target = defs.get(name)
            if target is None:
                return typed_node
            if depth >= _MAX_INLINE_DEPTH:
                return {"$ref": ref, "description": f"结构同上文的 {name}"}
            resolved: dict[str, Any] = resolve(target, depth + 1)
            # 保留引用处自带的 description，它通常比定义处更贴合上下文
            extras = {k: v for k, v in typed_node.items() if k != "$ref"}
            return {**resolved, **extras}

        return {key: resolve(value, depth) for key, value in typed_node.items() if key != "$defs"}

    result: dict[str, Any] = resolve({k: v for k, v in schema.items() if k != "$defs"}, 0)
    return result


def render_schema_instructions(output_model: type[BaseModel]) -> str:
    """生成附加到 instructions 末尾的 schema 说明。

    ⚠️ 这段文本必须**追加在 instructions 末尾**而不是插在开头——
    prompt 缓存要求前缀逐字节稳定（§9.8）。同理它不含任何时间戳或随机内容。

    文本中必须出现 "JSON" 字样：json_object 模式的多数实现要求 prompt 里
    提到 JSON，否则会直接报错。
    """
    schema = inline_refs(output_model.model_json_schema())
    rendered = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        "\n\n# 输出格式\n"
        "只输出一个符合下面 JSON Schema 的 JSON 对象。\n"
        "不要输出任何解释文字，不要用 markdown 代码块包裹，不要输出 schema 本身。\n"
        "所有字段都必须出现；没有值的可选字段填 null，没有元素的数组填 []。\n\n"
        f"```json\n{rendered}\n```"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 文本 → 模型
# ─────────────────────────────────────────────────────────────────────────────


def extract_json_object(text: str) -> str:
    """从模型输出里抠出 JSON 对象。

    即使明确要求不要用代码块，模型仍然经常包一层 ```json；也常见在 JSON
    前后附一句解释。这里按「代码块 → 首个平衡的花括号块 → 原文」逐级降级。
    """
    stripped = text.strip()

    fenced = _FENCE_RE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()

    start = stripped.find("{")
    if start == -1:
        return stripped

    # 即使文本已经以 { 开头也要走配平：模型常在 JSON 之后再补一句
    # "希望对你有帮助"，不截掉的话 Pydantic 会报 trailing characters

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]

    return stripped[start:]


def parse_output[T: BaseModel](text: str, output_model: type[T]) -> T:
    """把模型输出解析成目标类型。失败时抛出的异常信息会被回喂给模型。"""
    return output_model.model_validate_json(extract_json_object(text))


def format_validation_feedback(error: Exception) -> str:
    """把校验错误整理成给模型的修正提示。

    只保留字段路径与原因，不带 Python 栈——模型看到栈信息除了消耗 token
    没有任何帮助（同 §8.1 对 tool 错误的处理原则）。
    """
    if isinstance(error, ValidationError):
        problems = [
            f"  - 字段 {'.'.join(str(p) for p in item['loc']) or '(根)'}: {item['msg']}"
            for item in error.errors()[:10]
        ]
        detail = "\n".join(problems)
        return f"上一次输出不符合 schema，请修正后重新输出完整的 JSON：\n{detail}"
    return f"上一次输出不是合法 JSON（{error}）。请重新输出一个完整的 JSON 对象。"


# ─────────────────────────────────────────────────────────────────────────────
# 策略
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StructuredOutputStrategy[T: BaseModel]:
    """针对某个模型能力档位的结构化输出方案。"""

    mode: StructuredOutputMode
    output_model: type[T]
    agent_output_type: type[T] | AgentOutputSchemaBase | None
    """交给 `Agent(output_type=...)`。json_mode / prompt_only 下为 None——
    一旦设置，SDK 就会强行发送 json_schema 格式的 response_format。"""
    instructions_suffix: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_manual_parsing(self) -> bool:
        return self.agent_output_type is None

    def apply(self, settings: ModelSettings) -> ModelSettings:
        """把 response_format 之类的覆盖项合并进 ModelSettings。"""
        if not self.extra_body:
            return settings
        # extra_body 在上游被标注为 `Body`（等价于 object），并非一定是 Mapping
        existing = settings.extra_body if isinstance(settings.extra_body, Mapping) else {}
        merged = {**existing, **self.extra_body}
        return settings.resolve(ModelSettings(extra_body=merged))

    def parse(self, text: str) -> T:
        return parse_output(text, self.output_model)


def build_strategy[T: BaseModel](
    output_model: type[T], capabilities: ModelCapabilities
) -> StructuredOutputStrategy[T]:
    """按模型能力选择结构化输出路径。"""
    match capabilities.structured_output:
        case StructuredOutputMode.NATIVE_SCHEMA:
            return StructuredOutputStrategy(
                mode=StructuredOutputMode.NATIVE_SCHEMA,
                output_model=output_model,
                agent_output_type=AgentOutputSchema(output_model, strict_json_schema=True),
            )
        case StructuredOutputMode.JSON_MODE:
            return StructuredOutputStrategy(
                mode=StructuredOutputMode.JSON_MODE,
                output_model=output_model,
                agent_output_type=None,
                instructions_suffix=render_schema_instructions(output_model),
                extra_body={"response_format": {"type": "json_object"}},
            )
        case StructuredOutputMode.PROMPT_ONLY:
            return StructuredOutputStrategy(
                mode=StructuredOutputMode.PROMPT_ONLY,
                output_model=output_model,
                agent_output_type=None,
                instructions_suffix=render_schema_instructions(output_model),
            )


# ─────────────────────────────────────────────────────────────────────────────
# 执行：应用策略 + 带错误重试
# ─────────────────────────────────────────────────────────────────────────────


class _RunFn(Protocol):
    async def __call__(
        self, starting_agent: Agent[Any], input: str | list[TResponseInputItem]
    ) -> RunResult: ...


def apply_strategy[T: BaseModel](
    agent: Agent[Any], strategy: StructuredOutputStrategy[T]
) -> Agent[Any]:
    """把策略应用到 Agent 上，返回克隆体（不修改传入的 agent）。

    schema 说明追加在 instructions **末尾**：prompt 缓存要求前缀逐字节稳定，
    插在开头会让每次请求都缓存未命中（§9.8）。
    """
    instructions = agent.instructions
    if strategy.instructions_suffix:
        if not isinstance(instructions, str):
            msg = (
                "结构化输出的 json_mode / prompt_only 路径要求 instructions 是静态字符串，"
                "因为 schema 说明需要在编译期追加到末尾以保证 prompt 前缀稳定（§9.8）"
            )
            raise TypeError(msg)
        instructions = instructions + strategy.instructions_suffix

    return agent.clone(
        instructions=instructions,
        output_type=strategy.agent_output_type,
        model_settings=strategy.apply(agent.model_settings),
    )


@dataclass(frozen=True)
class StructuredRunResult[T: BaseModel]:
    output: T
    result: RunResult
    """原始 RunResult，编排层从中取 usage 与 new_items 做埋点。"""
    attempts: int
    """总共调用了几次模型。>1 说明触发了修正重试，值得记 warning。"""


async def run_structured[T: BaseModel](
    agent: Agent[Any],
    user_input: str | list[TResponseInputItem],
    *,
    strategy: StructuredOutputStrategy[T],
    max_retries: int = 2,
    run: _RunFn | None = None,
) -> StructuredRunResult[T]:
    """运行 Agent 并保证拿到合法的 `T`。

    `native_schema` 路径下模型侧已保证字段约束，通常一次就过；
    `json_mode` / `prompt_only` 路径下失败会把**具体的字段错误**回喂给模型重试。
    回喂错误信息而不是简单重跑很关键——后者只是碰运气，前者给了模型修正依据。

    `run` 参数是为了测试注入，默认走 `Runner.run`。
    """
    run_fn: _RunFn = run or Runner.run  # pyright: ignore[reportAssignmentType]
    prepared = apply_strategy(agent, strategy)

    current_input = user_input
    last_error: Exception | None = None
    last_raw = ""

    for attempt in range(1, max_retries + 2):
        result = await run_fn(starting_agent=prepared, input=current_input)

        if not strategy.needs_manual_parsing:
            # SDK 已按 schema 校验过，直接取
            return StructuredRunResult(
                output=result.final_output_as(strategy.output_model),
                result=result,
                attempts=attempt,
            )

        last_raw = str(result.final_output or "")
        try:
            parsed = strategy.parse(last_raw)
        except (ValidationError, ValueError) as error:
            last_error = error
            feedback = format_validation_feedback(error)
            log.warning(
                "structured_output.retry",
                agent=agent.name,
                mode=strategy.mode.value,
                attempt=attempt,
                error=str(error)[:200],
            )
            current_input = [
                *result.to_input_list(),
                {"role": "user", "content": feedback},
            ]
            continue

        return StructuredRunResult(output=parsed, result=result, attempts=attempt)

    raise StructuredOutputError(
        model_name=strategy.output_model.__name__,
        attempts=max_retries + 1,
        last_error=str(last_error),
        raw=last_raw,
    )
