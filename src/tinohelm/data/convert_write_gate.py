from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Generator

_convert_write_gate = threading.Lock()


@contextmanager
def hold_convert_write_gate() -> Generator[None, None, None]:
    with _convert_write_gate:
        yield
