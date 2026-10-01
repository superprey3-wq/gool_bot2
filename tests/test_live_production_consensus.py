from gool_bot2 import live_for_against_judge
from gool_bot2.production_signal_throughput import _live_consensus_snapshot


class _Judge:
    def __init__(self, decision: str):
        self.decision = decision

    def to_dict(self):
        return {
            "market": "GOAL_BEFORE_HT",
            "decision": self.decision,
            "judge_score": 70.0,
        }


def test_live_consensus_allows_two_of_three(monkeypatch):
    monkeypatch.setattr(live_for_against_judge, "evaluate_argument_judge", lambda _record: _Judge("BET"))
    record = {
        "match": {"minute": 24, "is_halftime": False, "is_finished": False},
        "live_v4_runtime": {
            "goal_before_ht": {
                "decision": "NO_BET",
                "policy_allowed": False,
            }
        },
    }
    snap = _live_consensus_snapshot(record, "BET")
    assert snap["bet_votes"] == 2
    assert snap["allowed"] is True


def test_live_consensus_blocks_single_brain_vote(monkeypatch):
    monkeypatch.setattr(live_for_against_judge, "evaluate_argument_judge", lambda _record: _Judge("NO_BET"))
    record = {
        "match": {"minute": 24, "is_halftime": False, "is_finished": False},
        "live_v4_runtime": {
            "goal_before_ht": {
                "decision": "NO_BET",
                "policy_allowed": False,
            }
        },
    }
    snap = _live_consensus_snapshot(record, "BET")
    assert snap["bet_votes"] == 1
    assert snap["allowed"] is False
