"""pytest config: importable package + a hermetic, deterministic test env.

Two jobs:
  1. Make the package importable without an install (path insert).
  2. Force the deterministic mock judges by default. Several tests assert exact
     money constants (net_profit == 265.0, all_margins_protected, etc). Those
     only hold against the MockNemotron / MockSpendJudge. If the shell that runs
     pytest happens to have NVIDIA_API_KEY exported (e.g. from sourcing
     arbiter.env), select_*_judge() picks the LIVE model and the constants drift
     nondeterministically — green or red depending on ambient env, which is not
     a real signal. We strip those keys for the test session so the suite is
     reproducible. Opt back into the live path explicitly with
     ARBITER_TEST_LIVE=1 for the handful of integration checks that want it.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_LIVE_MODEL_ENV = ("NVIDIA_API_KEY", "NVIDIA_NIM_KEY", "OPENROUTER_API_KEY")


@pytest.fixture(autouse=True, scope="session")
def _force_deterministic_judges():
    """Strip live-model credentials so unit tests hit the mock, unless opted in."""
    if os.environ.get("ARBITER_TEST_LIVE") == "1":
        yield
        return
    saved = {k: os.environ.pop(k, None) for k in _LIVE_MODEL_ENV}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
