import os

import pytest

# The tests use a fixed owner name; the app itself starts with none and asks for it.
os.environ.setdefault("BRAIN_OWNER_NAME", "Rahul Sharma")
os.environ.setdefault("BRAIN_ASSUME_ONLINE", "1")      # tests never depend on the real network


@pytest.fixture(autouse=True)
def _finish_background_work():
    """Background threads from one test must not write into the next test's folders."""
    yield
    import sys
    cfg = sys.modules.get("brain.config")
    if cfg and hasattr(cfg, "wait_background"):
        cfg.wait_background(8)
