#!/usr/bin/env python3
"""Fail unless the opt-in hook replaced the compiled Leader market source."""

from hpquant.service import snapshot
from hpquant_aeron_source import run_tick_engine


installed = getattr(snapshot, "run_tick_engine", None)
if installed is not run_tick_engine:
    raise RuntimeError(
        "hpquant.service.snapshot.run_tick_engine was not replaced by the Aeron adapter"
    )

print(
    "HPQUANT_COMPILED_HOOK result=SUCCESS "
    "target=hpquant.service.snapshot.run_tick_engine",
    flush=True,
)
