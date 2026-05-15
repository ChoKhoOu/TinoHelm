"""Data type converters for Binance Vision CSV → NautilusTrader native objects."""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import pandas as pd

logger = logging.getLogger(__name__)


class SchemaError(Exception):
    """CSV schema 不匹配（缺少必需列）"""
    pass


@runtime_checkable
class Converter(Protocol):
    """Converter 统一接口"""

    supports_chunked: bool

    def validate_schema(self, df: pd.DataFrame) -> None:
        """检查必需列是否存在，缺失则抛 SchemaError"""
        ...

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        """将完整 DataFrame 转换为 NT 数据对象列表"""
        ...

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        """处理单个 chunk（仅 supports_chunked=True 时使用）"""
        ...


# 注册表 — 导入时自动填充
CONVERTER_REGISTRY: dict[str, Converter] = {}


def register(data_type: str):
    """装饰器: 注册 converter 到 CONVERTER_REGISTRY"""
    def decorator(cls):
        CONVERTER_REGISTRY[data_type] = cls()
        return cls
    return decorator


def get_converter(data_type: str) -> Converter:
    """获取 converter，未注册则抛 ValueError"""
    if data_type not in CONVERTER_REGISTRY:
        raise ValueError(
            f"Unknown data_type '{data_type}'. "
            f"Available: {list(CONVERTER_REGISTRY.keys())}"
        )
    return CONVERTER_REGISTRY[data_type]


# 导入所有 converter 模块以触发注册
from tinohelm.data.converters import (  # noqa: E402, F401
    book_depth,
    book_ticker,
    funding_rate,
    index_price,
    klines,
    liquidation,
    mark_price,
    metrics,
    premium_index,
    trades,
)
