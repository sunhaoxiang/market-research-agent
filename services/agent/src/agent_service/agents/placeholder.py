"""尚未实现的子 Agent 占位（stock / fact_checker）。

`web_research` 已在 P2-6 接上真工具，`crypto_research` 已在 P3-9 接上。
其余任务仍走这里，好让编排层在接手前就能发出 agent_started / agent_completed。

**它刻意不假装成功。** `data_gaps` 写明尚未实现，缺口会走进报告的
「数据限制」章节，而不是一段编造的 summary。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_service.schemas.events import AgentProgressEvent, AgentProgressPayload
from agent_service.schemas.findings import ResearchFinding

if TYPE_CHECKING:
    from agent_service.orchestrator.executor import TaskContext
    from agent_service.orchestrator.state import ResearchState
    from agent_service.schemas.common import AgentName

NOT_IMPLEMENTED_GAP = "子 Agent 尚未实现（Phase 2-4），本任务未获取任何真实数据"


class PlaceholderRunner:
    """满足 `TaskRunner` 协议的空实现。"""

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id

    def model_id_for(self, agent: AgentName) -> str:
        del agent  # 真实实现会按 agent 的角色档位解析不同模型（§9.5）
        return self._model_id

    async def run(self, context: TaskContext, state: ResearchState) -> ResearchFinding:
        task = context.task
        # 发一条 progress：这是前端 Activity Panel 在「已开始未完成」区间里
        # 唯一能显示的东西，P1-12 要靠它验证渲染
        state.bus.emit(
            AgentProgressEvent,
            payload=AgentProgressPayload(
                agent=task.agent,
                task_id=task.id,
                message="占位实现：跳过数据获取",
            ),
        )
        return ResearchFinding(
            task_id=task.id,
            agent=task.agent,
            summary=f"（占位）本应执行：{task.objective}",
            data_gaps=[NOT_IMPLEMENTED_GAP],
        )
