# 项目知识库

Cage 知识库存储项目级的经验沉淀，Scout agent 在规划和执行阶段会扫描此目录。

## 目录结构

```
knowledge/
├── README.md         ← 本文件
├── decisions/        ← 架构决策记录 (ADR)
├── lessons/          ← 经验教训
├── patterns/         ← 项目已建立的模式
└── pitfalls/         ← 已知陷阱和解决方案
```

## 何时添加

| 场景 | 放在哪 |
|------|--------|
| 做了一个重要的架构/技术选型 | `decisions/` |
| 踩了一个坑，下次要避免 | `pitfalls/` |
| 发现了一个好用的模式/方法 | `patterns/` |
| 从一次失败/成功中提炼了教训 | `lessons/` |

## 命名约定

文件名格式：`NNN-简短描述.md`（如 `001-选择-postgresql.md`）
编号递增，便于排序。
