# TinoHelm

基于 [NautilusTrader](https://nautilustrader.io) 的量化交易平台。支持回测、模拟盘（sandbox）和实盘交易，后端为 FastAPI + Redis 任务队列 + PostgreSQL 持久化，客户端为 Rust LLM-first CLI。

## 快速开始

### 1. 启动后端服务

```bash
docker compose up -d
```

启动 **PostgreSQL**、**Redis** 和 **API 服务**（FastAPI，端口 8000）。

源码变更后重新构建：

```bash
docker compose up -d --build api
```

### 2. 安装 CLI

Rust CLI 是主要交互界面：单次命令、原始 JSON，以及给 LLM/自动化调用使用的稳定 `llm` envelope。

```bash
make
```

裸 `make` 会构建 release 产物；如果 `/usr/local/bin` 可写，就安装到 `/usr/local/bin/tino`，否则安装到 `~/.local/bin/tino`；随后会验证新 shell 能从 `PATH` 解析到刚安装的二进制。如果 `PATH` 仍然解析到旧的 `tino`，或者完全解析不到，默认安装会直接失败，不再给一个“看似安装成功但不能全局调用”的状态。显式传入 `BINDIR=...` 时，如果该目录不是当前 `PATH` 的优先命中目录，会按打包/测试安装处理；需要强制全局解析时，在调整 `PATH` 后用同一个 `BINDIR` 跑 `make verify-install`。

常用变体：

```bash
make build                       # 只构建：cli/target/release/tino
make package                     # 打包：dist/tino-<target>.tar.gz
make verify-install              # 验证已安装二进制和 PATH 解析
make BINDIR=/path/on/PATH        # 明确指定其他安装目录
make uninstall                   # 删除已安装的 tino
```

### 3. 使用方式

```bash
tino --help
tino -f llm api get /api/node/status
tino backtest list
tino backtest run <strategy> --symbol BTCUSDT-PERP --interval 5m --start 2025-02-01 --end 2025-03-01
tino factor list
tino signal list
```

## 入门 — 创建你的第一个策略

### 生成策略模板

```bash
tino strategy create my_strategy            # K线策略（默认）
tino strategy create my_hft_strategy -t tick  # 逐笔策略
```

模板文件生成在 `~/.tino/strategies/<name>.py`，可直接编辑。

### 策略类型

| 类型 | 触发方式 | 适用场景 |
|------|----------|----------|
| `bar` | `on_bar()` — 每根 K 线收盘时触发 | 动量、均值回归、多因子 — 大部分策略 |
| `tick` | `on_quote_tick()` / `on_trade_tick()` — 每笔行情触发 | 做市、高频、价差交易 |

### K 线策略模板

```python
class MyStrategyConfig(StrategyConfig):
    instrument_id: InstrumentId          # 如 "BTCUSDT-PERP.BINANCE"
    bar_type: BarType                    # 如 "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"
    trade_size: Decimal = Decimal("0.01")

class MyStrategy(Strategy):
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        setup_pause_support(self)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if is_paused(self):
            return
        # 在这里编写你的交易逻辑
        # bar.open, bar.high, bar.low, bar.close, bar.volume

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)
        self.close_all_positions(self.instrument_id)
```

### 逐笔策略模板

```python
class MyHftStrategyConfig(StrategyConfig):
    instrument_id: InstrumentId
    trade_size: Decimal = Decimal("0.01")

class MyHftStrategy(Strategy):
    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        # tick.bid_price, tick.ask_price, tick.bid_size, tick.ask_size
        pass

    def on_trade_tick(self, tick: TradeTick) -> None:
        # tick.price, tick.size, tick.aggressor_side
        pass
```

## 项目结构

```
cli/              Rust CLI（clap，LLM-first API caller）
src/tinohelm/     Python 后端（FastAPI + NautilusTrader）
src/web/          Next.js 前端（可选）
scripts/          工具脚本
docker-compose.yml
Dockerfile        API 容器
Dockerfile.web    前端容器（可选）
```

## 配置

策略文件在 `~/.tino/strategies/`，所有数据在 `~/.tino/data/`。

详细架构、约定和 API 参考见 [CLAUDE.md](CLAUDE.md)。
