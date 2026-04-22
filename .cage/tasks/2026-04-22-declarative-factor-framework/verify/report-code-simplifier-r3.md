# Code-Simplifier Report — Round 3

**任务**: 2026-04-22-declarative-factor-framework
**判定**: **PASS**（附 2 处简化已应用）

## 审查范围

本次任务 Execute 阶段引入的所有新代码（相对 HEAD），包括：
- 后端 34 个 factor/ 模块
- 前端 `app/factor/` 和 `app/factor/report/[id]/` 完整页面
- Alembic migration、API 路由、测试文件

## 已应用的简化（2 处，低影响）

### 1. `planner.py:252-260` — 删除 8 行死代码

原代码先初始化 `in_degree = {name: 0}` 并用 for 循环递增，然后用 `# Wait — recompute cleanly` 注释承认前面的计算废弃，直接覆盖：

```python
in_degree = {s.name: len(dependencies[s.name]) for s in specs}
```

已删除中间步骤及 `# Wait —` 遗留注释。保留单行表达式即可。

### 2. `data_layer.py:129` — `defaultdict` 局部导入提升至模块顶层

`from collections import defaultdict` 原放在 `load()` 方法体内。`collections` 是 stdlib 无延迟导入理由。移至模块顶层 import 块。

## 跳过的简化（有合理理由保留）

### `worker.py` vs `routes/factor.py` 的 Orchestrator 构建样板
两处约 8 行相似代码，但 `worker.py` 需要额外 monkey-patch `observer.start_span` 注入进度广播回调（worker-only 行为）。提取共享工厂会引入可选参数间接层，可读性下降。保留重复。

### `_run_one_kernel` 单行包装
`orchestrator.py:565-576` 看似一次性抽象，但注释说明保留原因是"模块顶层函数以便未来 ProcessPoolExecutor 迁移时可被 pickle"，合理。

## 正面观察

- **模块边界清晰**：依赖方向 types → alias → decorator → ast_check → registry → universe → data_layer → backend → engine → evaluation → cache → observer → worker，单向无环
- **AbstractBackend + PandasBackend 分层真实必要**：为 Polars/GPU backend 预留扩展点，已有测试
- **FactorCache 两级缓存**（values Parquet + eval JSON 独立命中）紧凑无过度分层
- **前端 hooks 拆分粒度恰当**：`useFactorList` + `useExplore` 无为单一功能创建无谓中间 hook

## 验证

```
.venv/bin/python -m pytest tests/ --tb=no
→ 1797 passed, 1 skipped, 5 warnings in 14.82s
```

简化后全量测试无回归，功能完全不变。

## Verdict: **PASS**

新引入的约 34 个框架模块整体质量高。发现并修复 2 处低影响熵增（planner 死代码、data_layer 延迟导入），无需更多简化。
