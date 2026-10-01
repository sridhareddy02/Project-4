"""Runs the whole evaluation and fails if any gate regresses. This is what CI uses as a quality bar."""
import json

from mktg_copilot import config
from mktg_copilot.evals import runner


def test_evaluation_gates_pass(tmp_path, monkeypatch, db_path):
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    assert runner.run_all(check=True) == 0
    result = json.loads((tmp_path / "eval.json").read_text())
    assert all(result["gates"].values())
    assert result["numbers"]["accuracy"] == 1.0
    assert result["grounding"]["pass_rate"] == 1.0
    assert sum(e["found"] for e in result["events"]) == 6
