import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from wxpredict import early_trial as trial  # noqa: E402


def test_the_experiment_refuses_training_or_baseline_leakage_and_window_changes():
    trial.validate_windows(["2025-07-15", "2026-10-11"], ["2026-10-12", "2026-10-14"], "2026-10-11")
    for train, test, baseline in [
        (["2026-10-12"], ["2026-10-13"], "2026-10-11"),
        (["2026-10-11"], ["2026-10-11"], "2026-10-10"),
        (["2026-10-11"], ["2026-10-15"], "2026-10-11"),
        (["2026-10-11"], ["2026-10-12"], "2026-10-12"),
        ([], ["2026-10-12"], "2026-10-11"),
    ]:
        with pytest.raises(ValueError):
            trial.validate_windows(train, test, baseline)
