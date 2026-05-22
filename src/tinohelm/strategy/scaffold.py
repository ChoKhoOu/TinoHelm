"""策略脚手架生成器。

生成策略参考模板，清晰说明每个回调的用途和常见用法。

模块分工:
    * ``STRATEGY_SCAFFOLD``  —— 超长模板字符串（本模块）
    * ``generate_scaffold``  —— 组合公开入口（本模块）
    * 校验 / 渲染 / 路径边界 —— :mod:`tinohelm.strategy.scaffold_helpers`
      （NT-free，便于单元测试）
"""
from __future__ import annotations

import logging

from tinohelm.strategy.scaffold_helpers import (
    render_scaffold,
    resolve_new_strategy_path,
    validate_identifier,
)

logger = logging.getLogger(__name__)

# ===========================================================================
# STRATEGY_SCAFFOLD — 策略参考模板
# ===========================================================================

STRATEGY_SCAFFOLD = '''"""
{name} — NautilusTrader 策略。

由 TinoHelm 脚手架自动生成。

NT 官方文档（LLM 请参考）:
- 策略开发:  https://nautilustrader.io/docs/latest/concepts/strategies/
- 订单类型:  https://nautilustrader.io/docs/latest/concepts/orders/
- 仓位管理:  https://nautilustrader.io/docs/latest/concepts/execution/
- 回测:      https://nautilustrader.io/docs/latest/concepts/backtesting/
- API 参考:  https://nautilustrader.io/docs/latest/api_reference/trading/strategy/

重要规则:
- HEDGING OMS: 每笔 submit_order = 独立仓位
- instrument_id, bar_type, order_id_tag, manage_stop 由 loader 注入，不要硬编码
- __init__ 中不能访问 self.clock/self.log/self.cache/self.portfolio（未初始化）
- 所有 subscribe_*() 必须在 on_start() 中调用
- 创建订单参数必须用 instrument.make_price() / make_qty()（精度不对 → OrderDenied）
- Quantity 无符号，不要传负值
- TriggerType.LAST_TRADE 不存在，用 TriggerType.LAST_PRICE
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import InstrumentId
from nautilus_trader.model.data import Bar, BarType, QuoteTick, TradeTick
from nautilus_trader.model.enums import (
    ContingencyType,
    OrderSide,
    OrderType,
    TimeInForce,
    TriggerType,
)
from nautilus_trader.model.events import (
    OrderDenied,
    OrderFilled,
    OrderRejected,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from tinohelm.strategy.state import stateful
from tinohelm.strategy.utils import is_paused, setup_pause_support


# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════

class {class_name}Config(StrategyConfig):
    """{class_name} 配置。

    由 loader 自动注入的字段（不要手动设置）:
        order_id_tag, manage_stop, manage_gtd_expiry,
        oms_type, external_order_claims

    在下方添加你的策略参数。
    """
    symbols: list[str] = []              # 交易品种，如 ["BTCUSDT-PERP"]
    interval: str = "5m"                 # K线周期
    symbol_params: dict = {{}}             # 按品种覆盖参数
    trade_size: Decimal = Decimal("0.01")
    sl_pct: float = 0.02                 # 止损距离（2%）
    tp_pct: float = 0.04                 # 止盈距离（4%）
    # --- 添加你的参数 ---
    # fast_period: int = 10
    # slow_period: int = 30


# ═══════════════════════════════════════════════════════════════════
# 按品种参数 (可选)
# ═══════════════════════════════════════════════════════════════════
# Jesse 格式 key（如 "BTC-USDT"）。loader 启动时自动校验品种是否存在。
# 未匹配的品种会 WARNING 并使用 Config 默认值。
#
# SYMBOL_PROFILES = {{
#     "BTC-USDT": {{"trade_size": Decimal("0.001"), "sl_pct": 0.015, "tp_pct": 0.03}},
#     "ETH-USDT": {{"trade_size": Decimal("0.01"),  "sl_pct": 0.02,  "tp_pct": 0.05}},
# }}


# ═══════════════════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════════════════

# @stateful 自动生成 on_save/on_load/on_reset，持久化列出的字段
@stateful("bar_count")
class {class_name}(Strategy):
    """{name} 策略。"""

    def __init__(self, config: {class_name}Config) -> None:
        super().__init__(config)
        self.symbols = config.symbols
        self.interval = config.interval
        self.trade_size = config.trade_size
        self.sl_pct = config.sl_pct
        self.tp_pct = config.tp_pct
        self._symbol_params = config.symbol_params

        # 在 on_start 中填充
        self._instruments: dict[str, Any] = {{}}
        self._bar_types: dict[str, Any] = {{}}

        # @stateful 持久化字段
        self.bar_count: int = 0

        # 仓位跟踪（用于清理退出订单）
        self._open_positions: dict[str, dict] = {{}}

        # ⚠️ 这里不能访问 self.log/self.clock/self.cache

    # ═══════════════════════════════════════════════════════════════
    # 生命周期回调
    # ═══════════════════════════════════════════════════════════════

    def on_start(self) -> None:
        """策略启动。在这里订阅数据、初始化指标。

        所有 subscribe_*() 必须在这里调用，否则对应的 on_*() 不会触发。

        可用的订阅:
            self.subscribe_bars(bar_type)              → on_bar()
            self.subscribe_quote_ticks(instrument_id)  → on_quote_tick()
            self.subscribe_trade_ticks(instrument_id)  → on_trade_tick()
            self.subscribe_order_book(instrument_id)   → on_order_book()
            self.subscribe_data(DataType(MyData))      → on_data()
            self.subscribe_signal("name")              → on_signal()

        可用的缓存 API:
            self.cache.instrument(instrument_id)       → Instrument
            self.cache.positions_open(instrument_id=id)→ list[Position]
            self.cache.orders_open(instrument_id=id)   → list[Order]
            self.cache.bar(bar_type, index=0)          → 最新 bar
            self.cache.bar_count(bar_type)             → int
            self.portfolio.account(venue)              → Account
            self.portfolio.unrealized_pnl(instrument_id)
            self.portfolio.net_exposures(venue)
        """
        from tinohelm.strategy.loader import normalize_symbol, make_bar_type_str

        for symbol in self.symbols:
            nt_sym = normalize_symbol(symbol)
            inst = self.cache.instrument(InstrumentId.from_str(nt_sym))
            if inst is None:
                self.log.error(f"品种未找到: {{nt_sym}}")
                continue
            self._instruments[symbol] = inst

            # 按品种参数覆盖（symbol_params 或 SYMBOL_PROFILES 均可）
            # sp = self._symbol_params.get(symbol, {{}})
            # local_sl = sp.get("sl_pct", self.sl_pct)

            bt = BarType.from_str(make_bar_type_str(symbol, self.interval))
            self._bar_types[symbol] = bt
            self.subscribe_bars(bt)

            # 如需 tick 级数据，取消对应注释:
            # self.subscribe_quote_ticks(inst.id)   → on_quote_tick()
            # self.subscribe_trade_ticks(inst.id)   → on_trade_tick()（需先 fetch trades 数据）

        if self._instruments:
            first_inst = next(iter(self._instruments.values()))
            self._venue = first_inst.venue
            self._currency = first_inst.quote_currency

        # L1 暂停支持
        setup_pause_support(self)

        # 风控: RiskGuardActor + LifecycleController 已在系统级通过
        # risk_engine.set_trading_state() 强制执行（HALTED/REDUCING）。
        # 策略无需自行管理 — 违规订单会直接 OrderDenied。

        # 可选: 定时器 → on_event() 中通过 TimeEvent 接收
        # import pandas as pd
        # self.clock.set_timer("rebalance", interval=pd.Timedelta(minutes=5))

        self.log.info(f"启动，品种: {{self.symbols}}")

    def on_stop(self) -> None:
        """策略停止。manage_stop=True 时 market_exit() 会先自动执行。

        这里做防御性清理。
        """
        for inst in self._instruments.values():
            self.cancel_all_orders(inst.id)
            self.close_all_positions(inst.id)

    # ----- L1 暂停: 拦截下单，不阻断信号计算 -----
    # 重写 submit_order / submit_order_list，暂停时所有订单被拦截。
    # 信号计算（on_bar 等）照常运行，指标保持更新，恢复后立刻可用。
    # 策略开发者不需要手动检查 is_paused — 这里自动兜底。

    def submit_order(self, order, **kwargs) -> None:
        if is_paused(self):
            self.log.debug(f"暂停中，订单已拦截: {{order.client_order_id}}")
            return
        super().submit_order(order, **kwargs)

    def submit_order_list(self, order_list, **kwargs) -> None:
        if is_paused(self):
            self.log.debug("暂停中，括号单已拦截")
            return
        super().submit_order_list(order_list, **kwargs)

    # on_resume()  — 从暂停恢复
    # on_reset()   — 回测间重置（@stateful 自动处理）
    # on_dispose() — 最终资源释放
    # on_degrade() — 进入降级状态
    # on_fault()   — 进入故障状态
    # on_save()    — 持久化状态（@stateful 自动生成）
    # on_load()    — 恢复状态（@stateful 自动生成）

    # ═══════════════════════════════════════════════════════════════
    # 数据回调
    # ═══════════════════════════════════════════════════════════════

    def on_bar(self, bar: Bar) -> None:
        """每根 bar 收盘时触发。核心交易逻辑入口。

        字段: bar.open, bar.high, bar.low, bar.close, bar.volume
        时间: bar.ts_init = 收盘时间（无未来偏差）
        多品种: bar.bar_type.instrument_id 识别品种

        常见用法:
          1) 直接在 bar 里算信号 + 下单（最简单）
          2) 算信号后存起来，在 on_quote_tick 里以精确价格执行（更精细）
          3) 用 bracket() 一次提交 entry + SL + TP（最完整）
        """
        self.bar_count += 1
        instrument_id = bar.bar_type.instrument_id
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            return

        # --- 你的交易逻辑 ---

        # ┌─────────────────────────────────────────────────────┐
        # │ 示例 1: 直接下市价单（最简单）                       │
        # └─────────────────────────────────────────────────────┘
        # if should_buy:
        #     order = self.order_factory.market(
        #         instrument_id=instrument_id,
        #         order_side=OrderSide.BUY,
        #         quantity=instrument.make_qty(self.trade_size),
        #     )
        #     self.submit_order(order)

        # ┌─────────────────────────────────────────────────────┐
        # │ 示例 2: 括号单 — 同时提交 entry + SL + TP          │
        # │ SL/TP 在开仓成交后自动激活（OTO 模式）              │
        # │ SL 和 TP 互相关联 — 一个成交自动取消另一个（OUO）   │
        # └─────────────────────────────────────────────────────┘
        # close_px = float(bar.close)
        # bracket = self.order_factory.bracket(
        #     instrument_id=instrument_id,
        #     order_side=OrderSide.BUY,
        #     quantity=instrument.make_qty(self.trade_size),
        #     # 开仓单
        #     entry_order_type=OrderType.MARKET,
        #     # 止损
        #     sl_trigger_price=instrument.make_price(close_px * (1 - self.sl_pct)),
        #     # 止盈
        #     tp_order_type=OrderType.LIMIT,
        #     tp_price=instrument.make_price(close_px * (1 + self.tp_pct)),
        #     tp_post_only=True,
        #     # OTO: 子单在母单成交后激活
        #     contingency_type=ContingencyType.OTO,
        # )
        # self.submit_order_list(bracket)

        # ┌─────────────────────────────────────────────────────┐
        # │ 示例 3: 信号反转平仓                                │
        # │ ⚠️ cancel_all_orders 必须在 close_position 之前     │
        # │    否则止损 + 平仓可能同时成交 → 意外反向持仓       │
        # └─────────────────────────────────────────────────────┘
        # pos = self._position(instrument_id)
        # if pos and pos.is_open and should_close:
        #     self.cancel_all_orders(instrument_id)
        #     self.close_position(pos)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """每次报价更新时触发（需在 on_start 中 subscribe_quote_ticks）。

        字段: tick.bid_price, tick.ask_price, tick.bid_size, tick.ask_size

        适合做精确价格入场 — 在 on_bar 中算出信号，在这里以实时报价下单。
        纯 bar 回测中此回调不触发（无 tick 数据）。
        """
        pass

    def on_trade_tick(self, tick: TradeTick) -> None:
        """每笔成交时触发（需 subscribe_trade_ticks + fetch trades 数据）。

        字段:
            tick.price           — 成交价 (Price)
            tick.size            — 成交量 (Quantity)
            tick.aggressor_side  — 主动方 (AggressorSide.BUYER / SELLER / NO_AGGRESSOR)
            tick.trade_id        — 成交 ID (TradeId)
            tick.ts_event        — 成交时间 (纳秒)
            tick.instrument_id   — 品种

        适合: 成交量分析、大单检测、微观结构策略。
        回测中 TradeTick 会触发订单撮合（比纯 Bar 更精确）。
        """
        # ┌─────────────────────────────────────────────────────┐
        # │ 示例: 大单检测 — 主动买入量超阈值时记录              │
        # └─────────────────────────────────────────────────────┘
        # from nautilus_trader.model.enums import AggressorSide
        # if (
        #     tick.aggressor_side == AggressorSide.BUYER
        #     and float(tick.size) > 10.0  # 自定义阈值
        # ):
        #     self.log.info(
        #         f"大单买入: {{tick.instrument_id}} "
        #         f"qty={{tick.size}} @ {{tick.price}}"
        #     )
        pass

    # on_order_book(book)      — 订单簿快照（需 subscribe_order_book）
    # on_data(data)            — 自定义数据（需 subscribe_data(DataType(...))）
    # on_historical_data(data) — request_bars() 响应（用于历史数据回填）

    # ═══════════════════════════════════════════════════════════════
    # 订单事件回调
    # ═══════════════════════════════════════════════════════════════
    #
    # 订单生命周期:
    #   submit_order()
    #     → OrderDenied  (NT 风控拒绝，未发到交易所)
    #     → OrderRejected (交易所拒绝)
    #     → OrderFilled   → PositionOpened / Changed / Closed
    #
    # 可用订单类型 (self.order_factory.*):
    #   .market()              市价单
    #   .limit()               限价单
    #   .stop_market()         止损触发单
    #   .stop_limit()          止损限价单
    #   .market_if_touched()   触价市价单
    #   .limit_if_touched()    触价限价单
    #   .trailing_stop_market() 追踪止损
    #   .bracket()             括号单 (entry + SL + TP)
    #
    # 时效: GTC(默认), IOC, FOK, GTD, DAY

    def on_order_filled(self, event: OrderFilled) -> None:
        """每次订单成交（含部分成交）时触发。

        字段:
            event.client_order_id  — 订单 ID
            event.order_side       — BUY / SELL
            event.last_qty         — 成交数量
            event.last_px          — 成交价格
            event.position_id      — 关联仓位
            event.instrument_id    — 品种

        常见用法:
          - 根据精确成交价挂止损（比 on_position_opened 中的 avg_px_open 更精确）
          - 滑点统计
          - 部分止盈后调整止损数量

        ⚠️ 不要在这里开新仓。开仓应由信号驱动（on_bar / on_quote_tick）。
        """
        pass

    def on_order_rejected(self, event: OrderRejected) -> None:
        """交易所拒绝订单。

        常见原因: 保证金不足、持仓限额、价格超范围。
        """
        self.log.warning(f"订单被拒绝: {{event.client_order_id}} — {{event.reason}}")
        self._cleanup_order_tracking(event.client_order_id)

    def on_order_denied(self, event: OrderDenied) -> None:
        """NT 风控引擎拒绝订单（未发到交易所）。

        常见原因: 精度不匹配（用 make_price/make_qty）、超最大名义值。
        """
        self.log.warning(f"订单被风控拒绝: {{event.client_order_id}} — {{event.reason}}")
        self._cleanup_order_tracking(event.client_order_id)

    # on_order_accepted(event)   — 交易所已接受
    # on_order_canceled(event)   — 已取消（可做重试逻辑）
    # on_order_expired(event)    — GTD/DAY 订单过期
    # on_order_updated(event)    — 修改成功（数量/价格）
    # on_order_submitted(event)  — 已提交到交易所
    # on_order_triggered(event)  — 止损单触发
    # on_order_event(event)      — 所有订单事件的通用回退

    # ═══════════════════════════════════════════════════════════════
    # 仓位事件回调
    # ═══════════════════════════════════════════════════════════════

    def on_position_opened(self, event: PositionOpened) -> None:
        """新仓位开立时触发（开仓单成交后）。

        常见用法: 挂止损/止盈保护单。
        如果用了 bracket() 括号单，SL/TP 已自动管理，这里可以跳过。

        仓位字段:
            position.instrument_id   品种
            position.side            LONG / SHORT / FLAT
            position.quantity        当前数量
            position.avg_px_open     平均开仓价
            position.entry           开仓方向 (OrderSide)
            position.ts_opened       开仓时间 (纳秒)
        """
        position = self.cache.position(event.position_id)
        if position is None:
            return

        iid = position.instrument_id
        instrument = self.cache.instrument(iid)
        if instrument is None:
            return

        entry_px = position.avg_px_open
        qty = position.quantity
        is_long = position.side.name == "LONG"
        pid = event.position_id
        exit_side = OrderSide.SELL if is_long else OrderSide.BUY

        # --- 计算退出价位（替换为你的逻辑）---
        if is_long:
            sl_px = entry_px * (1 - self.sl_pct)
            tp_px = entry_px * (1 + self.tp_pct)
        else:
            sl_px = entry_px * (1 + self.sl_pct)
            tp_px = entry_px * (1 - self.tp_pct)

        # 止损
        stop_order = self.order_factory.stop_market(
            instrument_id=iid,
            order_side=exit_side,
            quantity=qty,
            trigger_price=instrument.make_price(sl_px),
            trigger_type=TriggerType.LAST_PRICE,
            reduce_only=True,
        )
        self.submit_order(stop_order, position_id=pid)

        # 止盈
        tp_order = self.order_factory.limit(
            instrument_id=iid,
            order_side=exit_side,
            quantity=qty,
            price=instrument.make_price(tp_px),
            reduce_only=True,
            post_only=True,
        )
        self.submit_order(tp_order, position_id=pid)

        self._open_positions[pid.value] = {{
            "stop_oid": stop_order.client_order_id,
            "tp_oid": tp_order.client_order_id,
        }}
        self.log.info(
            f"开仓: {{pid}} {{'多' if is_long else '空'}} "
            f"数量={{qty}} @ {{entry_px:.4f}}"
        )

    def on_position_changed(self, event: PositionChanged) -> None:
        """仓位数量/方向变化时触发（部分平仓成交）。

        分批止盈时，部分成交会改变仓位数量。可能需要调整止损数量。
        """
        pass

    def on_position_closed(self, event: PositionClosed) -> None:
        """仓位完全平仓时触发。取消剩余退出订单，清理跟踪状态。

        仓位字段:
            position.realized_pnl    已实现盈亏 (Money，用 .as_double() 取 float)
            position.duration_ns     持仓时长 (纳秒)
            position.commissions()   手续费 dict
        """
        pid_str = event.position_id.value
        tracking = self._open_positions.pop(pid_str, None)

        if tracking:
            for key in ("stop_oid", "tp_oid"):
                oid = tracking.get(key)
                if oid:
                    order = self.cache.order(oid)
                    if order and not order.is_closed:
                        self.cancel_order(order)

        position = self.cache.position(event.position_id)
        if position:
            self.log.info(f"平仓: {{event.position_id}} 盈亏={{position.realized_pnl}}")

    # on_position_event(event) — 所有仓位事件的通用回退

    # ═══════════════════════════════════════════════════════════════
    # 信号与通用事件
    # ═══════════════════════════════════════════════════════════════

    def on_signal(self, signal) -> None:
        """接收轻量级信号 (int/float/str)。

        订阅: self.subscribe_signal("name") in on_start()
        发布: self.publish_signal(name="alert", value="high", ts_event=...)
        """
        pass

    def on_event(self, event) -> None:
        """通用事件回退 — 捕获所有未被专用回调处理的事件。

        常用于 TimeEvent（定时器，需在 on_start 中 set_timer）:
            from nautilus_trader.common.events import TimeEvent
            if isinstance(event, TimeEvent) and event.name == "rebalance":
                self._periodic_rebalance()
        """
        # ┌─────────────────────────────────────────────────────┐
        # │ 示例: 配合 on_start 中的 set_timer("rebalance")    │
        # └─────────────────────────────────────────────────────┘
        # from nautilus_trader.common.events import TimeEvent
        # if isinstance(event, TimeEvent) and event.name == "rebalance":
        #     for symbol, inst in self._instruments.items():
        #         pos = self._position(inst.id)
        #         self.log.info(f"定时检查 {{symbol}}: 持仓={{'有' if pos else '无'}}")
        pass

    # ═══════════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════════

    def _position(self, instrument_id: InstrumentId = None):
        """获取本策略在指定品种上的持仓。"""
        if instrument_id is None and self._instruments:
            instrument_id = next(iter(self._instruments.values())).id
        ps = self.cache.positions_open(instrument_id=instrument_id)
        my = [p for p in ps if str(p.strategy_id) == str(self.id)]
        return my[0] if my else None

    def _has_open_orders(self, instrument_id: InstrumentId = None) -> bool:
        """检查是否有未完成订单。"""
        if instrument_id is None and self._instruments:
            instrument_id = next(iter(self._instruments.values())).id
        return bool(self.cache.orders_open(instrument_id=instrument_id))

    def _cleanup_order_tracking(self, client_order_id) -> None:
        """清理被拒绝订单的跟踪记录。"""
        for tracking in self._open_positions.values():
            for key in ("stop_oid", "tp_oid"):
                if tracking.get(key) == client_order_id:
                    tracking.pop(key, None)
'''


# ===========================================================================
# 生成器
# ===========================================================================

from pathlib import Path  # noqa: E402  (after the multi-line template string)


def generate_scaffold(
    name: str,
    strategies_dir: str | Path,
    scaffold_type: str = "strategy",  # noqa: ARG001  (reserved for API compat)
) -> Path:
    """生成策略脚手架文件。

    Parameters
    ----------
    name:
        目标文件 basename（不含 ``.py`` 后缀）。必须是合法 Python 标识符，因为
        生成出来的文件会被 ``importlib`` 按 ``name`` 加载。
    strategies_dir:
        脚手架写入目录。不存在时自动创建。
    scaffold_type:
        前端 ``POST /strategies/create`` 的 ``type`` 字段透传参数。当前
        ``"strategy"`` 和 ``"portfolio"`` 均生成同一份 Strategy 模板，保留入
        参是为未来按类型分支（例如生成 portfolio.yaml + strategy.py 组合）
        留接口；传任意值都不会报错也不影响生成内容。

    Raises
    ------
    ValueError
        *name* 不是合法标识符，或路径解析后越过 *strategies_dir* 边界
        （防御性，已由 :func:`validate_identifier` 前置兜底）。
    FileExistsError
        目标文件已存在 —— 避免覆盖用户手写的策略。
    """
    validate_identifier(name)

    strategies_path = Path(strategies_dir)
    strategies_path.mkdir(parents=True, exist_ok=True)

    file_path = resolve_new_strategy_path(strategies_path, name)
    if file_path.exists():
        raise FileExistsError(f"Strategy file already exists: {file_path}")

    file_path.write_text(render_scaffold(name))
    logger.info(f"Generated strategy scaffold: {file_path}")
    return file_path
