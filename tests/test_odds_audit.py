from gool_bot2.odds_audit import odds_audit_rows, wilson_lower


def row(sport, odd, result, phase="PREMATCH", origin="prematch"):
    return {"sport": sport, "odd": odd, "result": result, "phase": phase, "origin": origin}


def test_wilson_lower_conservative_and_empty():
    assert wilson_lower(0, 0) == 0
    assert 0 < wilson_lower(70, 100) < .70


def test_odds_audit_uses_settled_prematch_and_correct_flat_stake_roi():
    rows = [
        row("football", 1.8, "won"),
        row("football", 1.8, "lost"),
        row("football", 1.8, "pending"),
        row("football", 1.8, "won", "LIVE"),
        row("football", 1.8, "won", "PREMATCH", "prematch_parlay"),
    ]
    hockey = [row("hockey", 2.0, "won", "PREMATCH", "multisport_prematch")]
    audit = odds_audit_rows(rows, hockey)
    foot = next(x for x in audit if x["sport"] == "football")
    assert foot["bets"] == 2
    assert foot["wins"] == 1
    assert round(foot["profit"], 8) == -0.2
    assert round(foot["roi"], 8) == -0.1
    assert abs(foot["approx_break_even"] - 1.0 / 1.8) < 1e-8
    assert not foot["sample_sufficient"]
    assert len(audit) == 2
