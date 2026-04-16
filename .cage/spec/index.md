# 项目规范索引

Cage 通过 scout agent 自动发现此目录下的规范文件，并在 P-E-V 工作流中注入到 executor/verifier 的上下文中。

## 目录结构

```
spec/
├── index.md          ← 本文件（规范总览）
├── guides/           ← 通用指南（编码规范、Git 规范等）
│   ├── index.md
│   └── conventions.md
└── {domain}/         ← 领域规范（按业务模块组织）
    ├── index.md
    └── *.md
```

## 如何添加规范

1. **通用指南**放在 `guides/` 下（如编码风格、命名约定、错误处理模式）
2. **领域规范**按业务模块建子目录（如 `auth/`、`api/`、`database/`）
3. 每个子目录建一个 `index.md` 作为该领域的入口
4. Scout agent 会扫描所有 `.md` 文件，按任务相关性选择注入

## 规范编写建议

- 用具体的代码示例，不要只写抽象原则
- 说明 WHY（为什么这样做），不只是 WHAT（做什么）
- 包含 DO 和 DON'T 的对比示例
- 保持每个文件 < 500 行，大文件拆分为多个
