import os

import pytest

LIVE = os.environ.get("LEDGERLY_LIVE") == "1"


def live(fn):
    """Calls real Gemma / TabPFN. Run with: LEDGERLY_LIVE=1 .venv/bin/pytest -m live"""
    return pytest.mark.live(pytest.mark.skipif(not LIVE, reason="set LEDGERLY_LIVE=1 to call real Gemma/TabPFN")(fn))
