"""模型抽象层（§9）。

业务 Agent 只声明角色（`ModelRole.BALANCED`），不出现任何 provider 名或模型名。
"换模型"因此是配置变更而非代码变更——这是开发期用 DeepSeek、上线切 OpenAI
能只改环境变量的前提。
"""

from agent_service.models.capabilities import (
    AdapterKind,
    ModelCapabilities,
    PeakSchedule,
    Pricing,
    ProviderId,
    StructuredOutputMode,
    TokenPrices,
)
from agent_service.models.catalog import (
    CATALOG,
    ModelEntry,
    get_entry,
    list_entries,
)

__all__ = [
    "CATALOG",
    "AdapterKind",
    "ModelCapabilities",
    "ModelEntry",
    "PeakSchedule",
    "Pricing",
    "ProviderId",
    "StructuredOutputMode",
    "TokenPrices",
    "get_entry",
    "list_entries",
]
