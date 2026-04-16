# 编码规范

> 本文件由 `cage init` 生成，请根据项目实际情况填写。
> Scout agent 会在 execute 阶段将此文件注入 executor 上下文。

## 命名约定

<!-- 填写项目的命名规则 -->

| 类型 | 风格 | 示例 |
|------|------|------|
| 文件名 | <!-- kebab-case / camelCase / snake_case --> | <!-- user-service.ts --> |
| 函数名 | <!-- camelCase --> | <!-- getUserById --> |
| 类名 | <!-- PascalCase --> | <!-- UserService --> |
| 常量 | <!-- UPPER_SNAKE_CASE --> | <!-- MAX_RETRY_COUNT --> |
| 接口/类型 | <!-- PascalCase --> | <!-- UserProfile --> |

## 导入顺序

<!-- 填写项目的 import 排序规则 -->

```
// 示例：
// 1. Node.js 内置模块
// 2. 第三方库
// 3. 项目内部模块（按路径深度）
// 4. 类型导入
```

## 错误处理模式

<!-- 填写项目统一的错误处理方式 -->

```
// 示例：
// - 业务错误：throw new AppError(code, message)
// - 外部调用：try/catch + 日志 + 降级
// - 验证错误：返回 Result<T, E> 类型
```

## 注释规范

<!-- 填写注释要求 -->

- 公共 API：<!-- JSDoc / 无要求 -->
- 复杂逻辑：<!-- 必须注释 WHY -->
- TODO 格式：<!-- TODO(author): description -->

## 测试规范

<!-- 填写测试相关约定 -->

- 测试框架：<!-- Jest / Vitest / Mocha -->
- 文件命名：<!-- *.test.ts / *.spec.ts -->
- 覆盖率要求：<!-- 80% / 无硬性要求 -->
- Mock 策略：<!-- 尽量用真实依赖 / 统一 mock -->

## 禁止模式

<!-- 列出项目中明确禁止的模式 -->

| 禁止 | 原因 | 替代方案 |
|------|------|----------|
| <!-- any 类型 --> | <!-- 丢失类型安全 --> | <!-- 使用 unknown + 类型守卫 --> |
| <!-- console.log --> | <!-- 泄漏到生产 --> | <!-- 使用 logger 模块 --> |
