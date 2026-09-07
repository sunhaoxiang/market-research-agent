"""跨模块共用的枚举与基类。

⚠️ 这些枚举与 `apps/web/db/schema.ts` 中的同名常量数组**必须保持一致**。
数据库侧有 CHECK 约束，不一致会在写入时报错（有意的设计：让漂移尽早暴露）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    """所有 schema 的基类。

    `extra` 保持默认的 ignore 而非 forbid：LLM 偶尔多返回一个字段不应导致整次研究失败，
    而缺字段会被 Pydantic 的必填校验抓住——那才是真正需要重试的情况（§9.4）。
    """

    model_config = ConfigDict(
        extra="ignore",
        use_enum_values=False,
        validate_assignment=True,
    )


class AgentName(StrEnum):
    """六个 Agent（§6.2）。裁剪理由见 §6.1。"""

    RESEARCH_MANAGER = "research_manager"
    CRYPTO_RESEARCH = "crypto_research"
    STOCK_RESEARCH = "stock_research"
    WEB_RESEARCH = "web_research"
    FACT_CHECKER = "fact_checker"
    REPORT_WRITER = "report_writer"


class QuestionType(StrEnum):
    CRYPTO = "crypto"
    STOCK = "stock"
    MACRO = "macro"
    COMPARE = "compare"
    GENERIC = "generic"


class AssetType(StrEnum):
    CRYPTO = "crypto"
    STOCK = "stock"


class Stage(StrEnum):
    """研究流程的四个阶段（§12.2 STAGE_CHANGED）。"""

    PLANNING = "planning"
    RESEARCHING = "researching"
    CHECKING = "checking"
    WRITING = "writing"


class SessionStatus(StrEnum):
    """会话状态机（§14.1）。包含 Stage 的全部取值加上首尾状态。"""

    PENDING = "pending"
    PLANNING = "planning"
    RESEARCHING = "researching"
    CHECKING = "checking"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EpistemicType(StrEnum):
    """认知类型（§15.3）。事实与推测分离的载体。

    报告主体应以 SOURCE_BACKED_FACT 为主；ANALYSIS / INFERENCE / PREDICTION
    必须出现在带标记的段落中，不得与事实段落混排。
    """

    FACT = "fact"
    """可验证的客观事实，但当前未附来源。"""

    SOURCE_BACKED_FACT = "source_backed_fact"
    """有来源支撑的事实。报告主体应以此为主。"""

    ANALYSIS = "analysis"
    """基于已获取数据的分析：数据可溯源，结论是推导。"""

    INFERENCE = "inference"
    """基于不完整信息的推测。"""

    PREDICTION = "prediction"
    """对未来的预测。"""

    OPINION = "opinion"
    """观点。"""


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONFLICTING = "conflicting"
    """多个来源给出不一致的值。"""
    UNSUPPORTED = "unsupported"
    """找不到支撑证据（不等于被证伪）。"""
    REFUTED = "refuted"
    """有来源明确证伪。"""


class SourceType(StrEnum):
    WEB = "web"
    NEWS = "news"
    OFFICIAL = "official"
    SEC = "sec"
    API = "api"
    DOCS = "docs"
    GITHUB = "github"
    SOCIAL = "social"


class SourceReliability(StrEnum):
    """来源可靠性分级（§15.2）。冲突时优先 primary。"""

    PRIMARY = "primary"
    """一手/权威：SEC EDGAR、公司 IR、项目官方文档、链上数据。"""
    SECONDARY = "secondary"
    """可信媒体/研究。"""
    AGGREGATOR = "aggregator"
    """聚合平台：CoinGecko、DefiLlama、FMP。"""
    UNKNOWN = "unknown"
    """无法判定：博客、论坛、社媒。仅有此类支撑的 claim 必须降级。"""


class ModelRole(StrEnum):
    """模型角色档位（§9.5）。Agent 声明角色，不硬编码型号。"""

    PLANNER = "planner"
    BALANCED = "balanced"
    FAST = "fast"
    WRITING = "writing"
