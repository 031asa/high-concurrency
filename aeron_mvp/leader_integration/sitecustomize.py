"""Opt-in bootstrap for the compiled hpquant package.

Python imports this module automatically when this directory is on PYTHONPATH.
The original CTP source remains untouched unless HPQUANT_MARKET_SOURCE=aeron-zmq.
"""

from __future__ import annotations

import os


if os.environ.get("HPQUANT_MARKET_SOURCE") == "aeron-zmq":
    import hpquant.service.snapshot as snapshot_module
    from hpquant_aeron_source import run_tick_engine

    original = getattr(snapshot_module, "run_tick_engine", None)
    if not callable(original):
        raise RuntimeError(
            "hpquant.service.snapshot.run_tick_engine is not replaceable; "
            "a Leader rebuild with a configurable md_process target is required"
        )
    snapshot_module.run_tick_engine = run_tick_engine
    print(
        "HPQUANT_AERON_SOURCE state=INSTALLED "
        f"endpoint={os.environ.get('HPQUANT_AERON_ZMQ_ENDPOINT', 'tcp://127.0.0.1:7101')}",
        flush=True,
    )
