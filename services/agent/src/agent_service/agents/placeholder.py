"""子 Agent 的临时占位（P1-11 → Phase 2 移除）。

真正的 `crypto_research` / `stock_research` / `web_research` 依赖 Phase 2-4 的
工具层，现在还不存在。但 P1-11 要打通「浏览器 → Next → Python → 事件流」这条
链路，需要执行阶段真的产生 agent_started / agent_completed 事件。

**它刻意不假装成功。** 每个任务的 `data_gaps` 都写明「子 Agent 尚未实现」，
于是这条缺口会一路走到报告的「数据限制」章节。相比返回一段编造的 summary，
这样做在 Phase 2 接手前不会有任何人（包括我自己）误以为研究流程已经能出结果。
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
