import pytest


@pytest.fixture(autouse=True)
def _finish_background_work():
    """Background threads from one test must not write into the next test's folders."""
    yield
    import sys
    cfg = sys.modules.get("brain.config")
    if cfg and hasattr(cfg, "wait_background"):
        cfg.wait_background(8)
