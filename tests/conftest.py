import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


@pytest.fixture(autouse=True)
def _weather_history_forgets():
    """weather_history keeps the newest prune and the archive's rows for the
    life of a process. A test process is many jobs' worth of fakes, so each
    test starts with nothing looked up."""
    import weather_history
    weather_history.reset()
    yield
    weather_history.reset()
