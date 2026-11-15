"""Verified, period-aware Flashscore evidence for LIVE goal scenarios.

Never transform cumulative match statistics into half statistics without
independent snapshots. Missing evidence remains None, not zero.
"""
from __future__ import annotations

import math
import sqlite3


def _pair(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    try:
        result = tuple(float(v) for v in value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if all(math.isfinite(v) and v >= 0 for v in result) else None


class ScenarioEvidence:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS progruz1_scenario_xg_samples(
            root_id TEXT NOT NULL, kickoff INTEGER NOT NULL,
            phase TEXT NOT NULL, bucket INTEGER NOT NULL,
            observed_at INTEGER NOT NULL,
            home_xg REAL NOT NULL, away_xg REAL NOT NULL,
            PRIMARY KEY (root_id, kickoff, phase, bucket))""")
        db.execute("""CREATE INDEX IF NOT EXISTS progruz1_scenario_xg_age
            ON progruz1_scenario_xg_samples(observed_at)""")
        db.commit()

    def observe(self, *, root, kickoff, now, phase, xg, pressure):
        """Return only observed first-half and second-half evidence.

        A second-half delta is computed from the FIRST observed 2H snapshot,
        not from an assumed xG=0 at half-time. It is a conservative lower
        bound on second-half activity, never an invented half-time split.
        """
        root, kickoff, now = str(root), int(kickoff), int(now)
        current = _pair(xg)
        if phase in ("1-й тайм", "2-й тайм") and current is not None:
            with self.db:
                self.db.execute("""INSERT INTO progruz1_scenario_xg_samples
                    (root_id,kickoff,phase,bucket,observed_at,home_xg,away_xg)
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(root_id,kickoff,phase,bucket) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    home_xg=excluded.home_xg,
                    away_xg=excluded.away_xg""",
                    (root, kickoff, phase, now // 30, now, *current))
                self.db.execute(
                    "DELETE FROM progruz1_scenario_xg_samples WHERE observed_at<?",
                    (now - 7 * 86400,))
        result = {
            "first_half_sot_total": None,
            "first_half_xg_total": None,
            "second_half_sot_by_team": None,
            "second_half_xg_delta": None,
            "recent_10m_xg_by_team": None,
            "recent_10m_xg_age": 0,
            "recent_5m_xg_by_team": None,
            "recent_5m_xg_age": 0,
        }
        # xG is a cumulative MATCH counter. Only a real same-half sample
        # from 8-10 minutes ago can prove fresh chance quality.
        if current is not None and phase in ("1-й тайм", "2-й тайм"):
            baseline = self.db.execute("""SELECT observed_at,home_xg,away_xg
                FROM progruz1_scenario_xg_samples
                WHERE root_id=? AND kickoff=? AND phase=?
                  AND observed_at BETWEEN ? AND ?
                ORDER BY observed_at DESC LIMIT 1""",
                (root, kickoff, phase, now-600, now-480)).fetchone()
            if baseline is not None:
                delta = (current[0] - float(baseline[1]),
                         current[1] - float(baseline[2]))
                if all(math.isfinite(v) and v >= 0 for v in delta):
                    result["recent_10m_xg_by_team"] = delta
                    result["recent_10m_xg_age"] = now-int(baseline[0])
        # Independent 4-6 minute xG increase: allows a strong new
        # attack to qualify without waiting 10 full minutes. It is
        # never reconstructed from whole-match xG or guessed.
        if current is not None and phase in ("1-й тайм", "2-й тайм"):
            fresh = self.db.execute("""SELECT observed_at,home_xg,away_xg
                FROM progruz1_scenario_xg_samples
                WHERE root_id=? AND kickoff=? AND phase=?
                  AND observed_at BETWEEN ? AND ?
                ORDER BY observed_at DESC LIMIT 1""",
                (root, kickoff, phase, now-360, now-240)).fetchone()
            if fresh is not None:
                delta = (current[0] - float(fresh[1]),
                         current[1] - float(fresh[2]))
                if all(math.isfinite(v) and v >= 0 for v in delta):
                    result["recent_5m_xg_by_team"] = delta
                    result["recent_5m_xg_age"] = now-int(fresh[0])
        if phase != "2-й тайм":
            return result
        try:
            # GoalPressure keeps genuine first-half snapshots even when
            # a fixture subsequently enters the second half.
            row = self.db.execute("""SELECT sot_home,sot_away
                FROM goal_live_pressure
                WHERE root_id=? AND kickoff=? AND phase='1-й тайм'
                ORDER BY observed_at DESC LIMIT 1""",
                (root, kickoff)).fetchone()
            if row is not None:
                result["first_half_sot_total"] = int(row[0]) + int(row[1])
        except sqlite3.OperationalError:
            pass
        first_xg = self.db.execute("""SELECT home_xg,away_xg
            FROM progruz1_scenario_xg_samples
            WHERE root_id=? AND kickoff=? AND phase='1-й тайм'
            ORDER BY observed_at DESC LIMIT 1""",
            (root, kickoff)).fetchone()
        if first_xg is not None:
            result["first_half_xg_total"] = float(first_xg[0]) + float(first_xg[1])
        if isinstance(pressure, dict):
            # Genuine 2H deltas, not cumulative full-match totals.
            row = pressure.get("sot_by_team")
            if isinstance(row, tuple) and len(row) == 2:
                result["second_half_sot_by_team"] = row
        earliest = self.db.execute("""SELECT home_xg,away_xg
            FROM progruz1_scenario_xg_samples
            WHERE root_id=? AND kickoff=? AND phase='2-й тайм'
            ORDER BY observed_at ASC LIMIT 1""",
            (root, kickoff)).fetchone()
        if earliest is not None and current is not None:
            delta = (current[0] - float(earliest[0]),
                     current[1] - float(earliest[1]))
            if min(delta) >= 0:
                result["second_half_xg_delta"] = delta
        return result
