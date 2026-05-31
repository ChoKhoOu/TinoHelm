#!/usr/bin/env python
"""Probe the *installed* NautilusTrader — the only source of truth.

TinoHelm never pins an NT version (the user upgrades NT often), so every
capability question must be answered against whatever NT is actually in the
venv right now — not a GitHub tag, not memory.

Usage:
    python nt_probe.py                 # version + source root + adapters
    python nt_probe.py <symbol>        # also grep the NT source tree for <symbol>

Run with the project's interpreter, e.g.:
    .venv/bin/python .claude/skills/nt-capability-probe/scripts/nt_probe.py MessageBusConfig
"""

from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    try:
        import nautilus_trader
    except ImportError:
        print("nautilus_trader not importable in this interpreter.", file=sys.stderr)
        print("Run with the project venv, e.g. .venv/bin/python ...", file=sys.stderr)
        return 1

    root = os.path.dirname(nautilus_trader.__file__)
    print(f"NT version (measured this run): {nautilus_trader.__version__}")
    print(f"NT source root: {root}")

    adapters_dir = os.path.join(root, "adapters")
    if os.path.isdir(adapters_dir):
        adapters = sorted(
            d
            for d in os.listdir(adapters_dir)
            if os.path.isdir(os.path.join(adapters_dir, d)) and not d.startswith("_")
        )
        print(f"adapters ({len(adapters)}): {', '.join(adapters)}")

    if len(argv) > 1:
        symbol = argv[1]
        print(f"\n=== grep '{symbol}' under {root} (defs first) ===")
        # NT is a Cython package: core types live in .pyx/.pxd, configs in .py,
        # stubs in .pyi. ripgrep skips .pyx/.pxd by default, so we MUST pass
        # explicit globs or definitions in compiled modules vanish silently.
        exts = ("py", "pyx", "pxd", "pyi")
        rg = subprocess.run(["which", "rg"], capture_output=True, text=True)
        if rg.returncode == 0:
            # --no-ignore is critical: NT lives under .venv/, which is
            # gitignored, so ripgrep's default .gitignore-respecting behaviour
            # silently returns ZERO hits inside site-packages.
            cmd = ["rg", "-n", "--no-heading", "--no-ignore"]
            for e in exts:
                cmd += ["-g", f"*.{e}"]
            cmd += [symbol, root]
        else:
            cmd = ["grep", "-rn"]
            for e in exts:
                cmd += [f"--include=*.{e}"]
            cmd += [symbol, root]
        result = subprocess.run(cmd, capture_output=True, text=True)
        lines = result.stdout.splitlines()
        # Surface likely definitions (class/def/cdef) ahead of mere usages.
        defs = [ln for ln in lines if any(k in ln for k in ("class ", "def ", "cdef ", "cpdef "))]
        others = [ln for ln in lines if ln not in defs]
        for ln in (defs + others)[:60]:
            print(ln)
        total = len(lines)
        if total > 60:
            print(f"... ({total - 60} more hits; refine with codegraph_search/codegraph_node)")
        if total == 0:
            print("(no hits — maybe renamed in this version? try a near-name or codegraph_search)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
