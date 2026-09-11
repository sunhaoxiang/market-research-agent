# Eval 对比 `p7-8-fixture-compare`

- schema: `1` kind=`compare`
- 时间: 2026-09-11T00:00:00+00:00
- git: `p7-8-fixture`
- 核心指标：幻觉率（越低越好）、引用覆盖率（越高越好）
- 结论：**`deepseek-flash` 综合更好**

## 综合排名

| 排名 | 标签 | 综合分 | pass_rate | 模型 |
| --- | --- | --- | --- | --- |
| 1 | `deepseek-flash` | 0.8889 | 1.0000 | balanced=deepseek:deepseek-flash, fast=deepseek:deepseek-flash, planner=deepseek:deepseek-flash, writing=deepseek:deepseek-flash |
| 2 | `glm-5.3` | 0.1111 | 0.9600 | balanced=zhipu:glm-5.3, fast=zhipu:glm-5.3, planner=zhipu:glm-5.3, writing=zhipu:glm-5.3 |

## 指标对照

| 指标 | 极性 | `deepseek-flash` | `glm-5.3` | 最优 |
| --- | --- | --- | --- | --- |
| `agent_routing_accuracy` | 高更好 | 0.9300 | **0.9500** | `glm-5.3` |
| `citation_coverage` | 高更好 | **0.9700** | 0.9100 | `deepseek-flash` |
| `citation_validity` | 高更好 | **0.9400** | 0.8800 | `deepseek-flash` |
| `hallucination_rate` | 低更好 | **0.0200** | 0.0900 | `deepseek-flash` |
| `numeric_accuracy` | 高更好 | **0.9800** | 0.9600 | `deepseek-flash` |
| `latency_p50_s` | 低更好 | 12.0000 | **8.0000** | `glm-5.3` |
| `pass_rate` | 高更好 | **1.0000** | 0.9600 | `deepseek-flash` |
