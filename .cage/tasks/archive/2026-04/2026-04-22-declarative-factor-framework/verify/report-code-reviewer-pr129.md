## 代码审查报告 — PR #129 feat/declarative-factor-framework

**审查文件数**：9
**问题总数**：9（含 1 HIGH、4 MEDIUM、4 LOW）

### 按严重程度
- CRITICAL: 0
- HIGH: 1（应该修复）
- MEDIUM: 4（考虑修复）
- LOW: 4（可选）

---

### Stage 1: 规格合规

**通过。** 审查范围聚焦于 PR 要求的 9 个集成点文件。已覆盖：8 个新 API 端点（factor.py）、路由注册（app.py）、DB migration + ORM model（011 + models.py）、EventBridge channel 迁移（bridge.py）、config 新增（config.py）、catalog 新增函数（catalog.py）、pipeline 双写（pipeline.py）、迁移脚本（migrate_funding_json_to_parquet.py）。所有声明的功能均有对应实现。

---

### 问题列表

---

**[HIGH] `submit_run` 不验证 `factor_name` 是否存在于 registry**

File: `src/tinohelm/api/routes/factor.py:248-281`

Issue: `POST /api/factor/run` 直接写入 DB 并入队，不做任何 registry 查询。客户端传入不存在的 `factor_name` 时，job 被入队并消耗 worker 资源，最终在 `_run_orchestrator` 内部失败并将 `FactorRun` 标记为 `failed`，产生无意义的 DB 记录并消耗 CPU 线程。对比 `/api/factor/explore`（第 168–169 行）已正确做了 `registry.get_spec()` 守卫。

Fix:
```python
# 在 submit_run 内 run = FactorRun(...) 之前插入：
from tinohelm.factor.registry import Registry
registry = Registry()
registry.scan()
if registry.get_spec(req.factor_name) is None:
    raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")
```

---

**[MEDIUM] `list_runs` 的 `.where()` 在 `.limit()` 之后应用，逻辑正确但可读性存在误导**

File: `src/tinohelm/api/routes/factor.py:295-297`

```python
stmt = select(FactorRun).order_by(FactorRun.created_at.desc()).limit(limit)
if factor_name:
    stmt = stmt.where(FactorRun.factor_name == factor_name)
```

Issue: 验证确认 SQLAlchemy Core 会将 WHERE 子句移至 LIMIT 之前（最终 SQL 正确），但这种写法违反"先 WHERE 再 LIMIT"的惯用模式，容易让维护者误以为这是一个 bug（先取 20 条再过滤），或在将来切换 ORM 时引入真实 bug。

Fix:
```python
stmt = select(FactorRun).order_by(FactorRun.created_at.desc())
if factor_name:
    stmt = stmt.where(FactorRun.factor_name == factor_name)
stmt = stmt.limit(limit)
```

---

**[MEDIUM] `recover_interrupted_jobs` 的 Redis `LPUSH` 不保证原有队列顺序**

File: `src/tinohelm/factor/worker.py:94-97`

```python
await rds.delete(QUEUE_KEY)
for run_id in queued_ids:
    await rds.lpush(QUEUE_KEY, run_id)
```

Issue: `lpush` 是头部插入，循环 `[A, B, C]` 后队列实际顺序是 `[C, B, A]`。原先由 `POST /run` 按时间顺序入队的 job 重启后顺序被反转，时间早的 job 反而最后被处理。对于优先级不敏感的场景是低危，但与直觉相反。

Fix: 使用 `rpush` 保持 FIFO 顺序，或一次性 `rpush(*queued_ids)`（Redis 原子操作）：
```python
if queued_ids:
    await rds.delete(QUEUE_KEY)
    await rds.rpush(QUEUE_KEY, *queued_ids)
```

---

**[MEDIUM] `explore_factor` 每次请求都实例化完整的 Orchestrator 栈，无连接/资源复用**

File: `src/tinohelm/api/routes/factor.py:143-215`

Issue: `Registry()`、`DataLayer()`、`PandasBackend()`、`Evaluator()`、`FactorCache()`、`Observer()` 每次 HTTP 请求都重新实例化。对于高频调用（前端实时探索）这会造成重复磁盘扫描和对象分配。`_run_orchestrator` worker 路径同样如此，但异步 job 场景频率较低，影响可接受。

Fix: 考虑在 `app.py` 的 lifespan 中实例化一个 `Orchestrator` 并通过 `deps` 注入，或为 `Registry` 单独增加进程级缓存（`Registry.scan()` 结果生命周期≈进程生命周期）。短期内至少可在 `list_factors` 和 `explore_factor` 间共享 `Registry` 实例。

---

**[MEDIUM] `PathSettings.factor_cache` 配置项声明后未被使用**

File: `src/tinohelm/core/config.py:44`

```python
factor_cache: Path = Path.home() / ".tino" / "factor_cache"
```

Issue: `worker.py:328` 和 `factor.py:191` 均硬编码 `Path(catalog_path) / ".factor_cache"` 而不读取 `settings.paths.factor_cache`。`FactorCache` 在 `cache.py:151` 有一处间接使用，但 `factor.py` 和 `worker.py` 两个主要入口都绕过它，导致实际使用的 cache 路径与配置项声明不一致，用户无法通过配置文件控制 cache 位置。

Fix: 在 `worker.py` 和 `factor.py` 中替换：
```python
cache_dir = Path(settings.paths.catalog) / ".factor_cache"
# 改为：
cache_dir = Path(settings.paths.factor_cache)
```

---

**[LOW] `factor.router` 被归类为"state-changing"并加上 auth，但 `/list`、`/universes`、`/symbols`、`/runs`、`/report/{id}` 是只读端点**

File: `src/tinohelm/api/app.py:157`

```python
app.include_router(factor.router, dependencies=_auth_deps)
```

Issue: 注释明确写"Read-only routers — no auth required"，`data.router`、`strategy.router` 等只读路由均无 auth。`/api/factor/list`、`/api/factor/universes`、`/api/factor/symbols`、`/api/factor/runs`、`/api/factor/report/{id}` 无副作用，应与 `data.router` 同等对待。`/explore`（触发 CPU 计算）、`/run`（写 DB + Redis）、`/create`（写文件系统）三个端点确实应该鉴权。现状是整个 router 统一加了 auth，对前端查询造成不必要摩擦，且与已有的 `data.router` 无 auth 风格不一致。

Fix: 将 factor router 拆分为只读/变更两组（或在端点级别单独加 `Depends(verify_api_key)`），或接受现状并在注释中说明原因。

---

**[LOW] `_DEFAULT_FACTORS_DIR` 和 `Registry._DEFAULT_USER_DIR` / `Universe._DEFAULT_UNIVERSE_DIR` 路径定义重复**

File: `src/tinohelm/api/routes/factor.py:35`

```python
_DEFAULT_FACTORS_DIR = Path.home() / ".tino" / "research" / "factors"
```

Issue: 与 `factor/registry.py:38` 中 `_DEFAULT_USER_DIR = Path.home() / ".tino" / "research" / "factors"` 完全相同。路径常量重复定义，若日后变更目录结构需要修改两处，容易产生漂移。

Fix: `factor.py` 应直接从 `Registry` 或统一常量模块导入该路径，而非重复定义。

---

**[LOW] migration `011` 的 `down_revision` 链接到 `010` 正确，但 CLAUDE.md 文档未更新**

File: `src/tinohelm/db/migrations/versions/011_add_factor_runs.py:14`

Issue: 审查代码层面无问题（chain: `007→008→009→010→011` 完整）。但 `CLAUDE.md` 项目说明仍写 "Migration chain: None → '001' → ... → '007'"，新加入的 008/009/010/011 四个 migration 未记录。这是文档偏差，不影响运行时行为，但会误导未来开发者。

Fix: 更新 `CLAUDE.md` 中 migration chain 说明到 "... → '007' → '008' → '009' → '010' → '011'"。（低优先级，可在 PR 描述中备注）

---

**[LOW] `create_factor` 的响应不包含 HTTP 201 状态码**

File: `src/tinohelm/api/routes/factor.py:353`

Issue: `POST /api/factor/create` 成功创建资源时返回 200（FastAPI 默认），RESTful 惯例应返回 201 Created。所有其他 `POST` 创建资源的端点（`submit_run` 返回 `{run_id, status}`）也未显式声明 201，但 `create_factor` 最为明显因为它创建了文件系统资源。

Fix: 添加 `status_code=201` 到装饰器：`@router.post("/create", status_code=201)`

---

### 正面观察

1. **NaN/Infinity 防护完整**：`evaluator.py` 中的 `_scrub_result` 函数在 Evaluator 的两个返回路径（第 253、330 行）均调用，确保写入 PostgreSQL JSON 列的 `EvalResult` 不含非法浮点值，完全遵守 CLAUDE.md 的约束。

2. **EventBridge channel 迁移干净**：`bridge.py` 中新增 `"tino:factor:": "factor."` 映射，前端 `notification-router.ts` 已对应更新为 `factor.completed`/`factor.failed`，旧 `research.*` 事件类型在前端无残留引用，迁移完整。

3. **Alembic migration 链正确**：`011` 的 `down_revision = "010"` 精准接链，`upgrade()` / `downgrade()` 对称，两个 index 均在 downgrade 时先删除再删表，操作顺序正确。

4. **DateTime 处理遵守约定**：`worker.py` 使用 `datetime.now(UTC).replace(tzinfo=None)` 写入 DB，显式 strip timezone 避免 asyncpg 的 aware datetime 报错，与 CLAUDE.md 的 "TIMESTAMP WITHOUT TIME ZONE" 约束一致。

5. **recover_interrupted_jobs 的 DB-first 顺序**：先 commit DB 状态变更，再操作 Redis 队列，确保崩溃时 DB 是 ground truth，重启时能正确重建队列，思路正确。

6. **`/api/factor/create` 的路径遍历防护良好**：`re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name)` 严格限定文件名为合法 Python 标识符，排除 `../`、绝对路径、空格等路径遍历手段，即使在没有额外 chroot 的情况下写文件也是安全的。

7. **双写策略保留向后兼容**：`pipeline.py` 的 `_write_funding_rates` 同时写入 Parquet（新主路径）和 JSON（backward-compat），迁移脚本提供手动触发的 `--delete-json` 选项而非强制删除，对存量部署友好。

---

### 判定

COMMENT

VerifyPass: code-reviewer
Verdict: PASS
