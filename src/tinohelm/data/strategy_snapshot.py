"""StrategySnapshot — generic container for strategy signal/indicator publishing."""
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass


@customdataclass
class StrategySnapshot(Data):
    """Generic strategy signal snapshot.

    Strategies publish their custom factors/indicators via this container.
    ``fields_json`` holds a JSON-serialized dict of arbitrary key-value pairs,
    supporting nested dicts for section grouping in the frontend.
    """
    strategy_id: str
    instrument_id: str
    fields_json: str
