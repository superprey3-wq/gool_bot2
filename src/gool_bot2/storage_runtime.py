from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

_ROTATED_ARCHIVE_RE = re.compile(r"^\d{8}T\d{6}Z$")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def runtime_root() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def prematch_cache_dir() -> Path:
    return Path(os.getenv("PREMATCH_CACHE_DIR", str(runtime_root() / "live" / "prematch_cache")))


def _match_id(record: dict[str, Any]) -> str:
    return str(((record.get("match") or {}).get("flashscore_event_id") or record.get("prematch_ref") or "")).strip()


def _prematch_ready(ctx: dict[str, Any]) -> bool:
    minimum = max(1, int(os.getenv("ANOTHER_GOAL_PREMATCH_MIN_TEAM_MATCHES", "5")))
    return bool(
        isinstance(ctx, dict)
        and len(ctx.get("home_recent") or []) >= minimum
        and len(ctx.get("away_recent") or []) >= minimum
    )


class PrematchStore:
    """Small persistent sidecar for one-time PREMATCH history per live match."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else prematch_cache_dir()

    def _path(self, match_id: str) -> Path:
        safe = _SAFE_ID_RE.sub("_", str(match_id).strip())[:180] or "unknown"
        return self.root / f"{safe}.json"

    def load(self, match_id: str) -> dict[str, Any]:
        if not match_id:
            return {}
        path = self._path(match_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def save(self, match_id: str, context: dict[str, Any]) -> bool:
        if not match_id or not isinstance(context, dict) or not context:
            return False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(match_id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(context, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
            return True
        except Exception as exc:
            print(f"PREMATCH_STORE_SAVE_ERROR match={match_id} err={type(exc).__name__}:{exc}", flush=True)
            return False

    def delete(self, match_id: str) -> None:
        if not match_id:
            return
        try:
            self._path(match_id).unlink(missing_ok=True)
        except Exception:
            pass

    def prune(self, max_age_hours: float | None = None) -> tuple[int, int]:
        age_hours = float(os.getenv("PREMATCH_CACHE_RETENTION_HOURS", "8")) if max_age_hours is None else float(max_age_hours)
        cutoff = time.time() - max(1.0, age_hours) * 3600.0
        removed = 0
        freed = 0
        if not self.root.exists():
            return removed, freed
        for path in self.root.glob("*.json"):
            try:
                stat = path.stat()
                if stat.st_mtime >= cutoff:
                    continue
                size = stat.st_size
                path.unlink()
                removed += 1
                freed += size
            except FileNotFoundError:
                continue
            except Exception:
                continue
        return removed, freed


def hydrate_prematch(record: dict[str, Any], store: PrematchStore | None = None) -> bool:
    current = record.get("prematch_context") or {}
    if _prematch_ready(current):
        return False
    mid = _match_id(record)
    if not mid:
        return False
    ctx = (store or PrematchStore()).load(mid)
    if not ctx:
        return False
    record["prematch_context"] = ctx
    return True


def _read_tail(path: Path, max_bytes: int) -> bytes:
    size = path.stat().st_size
    start = max(0, size - max(1, int(max_bytes)))
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read()
    if start and b"\n" in data:
        data = data.split(b"\n", 1)[1]
    return data


def trim_file_tail(path: Path, keep_bytes: int) -> int:
    """Trim a text/jsonl file in-place to a complete-line tail and return freed bytes."""
    try:
        before = path.stat().st_size
    except FileNotFoundError:
        return 0
    keep = max(4096, int(keep_bytes))
    if before <= keep:
        return 0
    try:
        data = _read_tail(path, keep)
        with path.open("wb") as handle:
            handle.write(data)
        after = len(data)
        return max(0, before - after)
    except Exception as exc:
        print(f"STORAGE_TRIM_ERROR file={path} err={type(exc).__name__}:{exc}", flush=True)
        return 0


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except Exception:
            pass
    return total


def purge_rotated_live_archives(runtime: Path) -> tuple[int, int]:
    archive_root = runtime / "raw" / "archive"
    if not archive_root.exists():
        return 0, 0
    removed = 0
    freed = 0
    for child in list(archive_root.iterdir()):
        if not child.is_dir() or not _ROTATED_ARCHIVE_RE.match(child.name):
            continue
        try:
            freed += _dir_size(child)
            shutil.rmtree(child)
            removed += 1
        except Exception as exc:
            print(f"STORAGE_ARCHIVE_DELETE_ERROR path={child} err={type(exc).__name__}:{exc}", flush=True)
    try:
        if archive_root.exists() and not any(archive_root.iterdir()):
            archive_root.rmdir()
    except Exception:
        pass
    return removed, freed


def bootstrap_prematch_from_raw(raw_dir: Path, store: PrematchStore) -> int:
    max_bytes = int(os.getenv("PREMATCH_BOOTSTRAP_BYTES", str(12 * 1024 * 1024)))
    saved: set[str] = set()
    if not raw_dir.exists():
        return 0
    files = sorted(raw_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:4]
    for path in files:
        try:
            data = _read_tail(path, max_bytes)
        except Exception:
            continue
        for raw in data.splitlines():
            try:
                record = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            mid = _match_id(record)
            ctx = record.get("prematch_context") or {}
            if not mid or mid in saved or not isinstance(ctx, dict) or not ctx:
                continue
            if store.save(mid, ctx):
                saved.add(mid)
    return len(saved)


def cleanup_raw_files(raw_dir: Path, retention_minutes: int | None = None) -> tuple[int, int]:
    retention = int(os.getenv("RAW_LIVE_RETENTION_MINUTES", "120")) if retention_minutes is None else int(retention_minutes)
    cutoff = time.time() - max(30, retention) * 60.0
    removed = 0
    freed = 0
    if not raw_dir.exists():
        return removed, freed
    files = sorted(raw_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    newest = files[0] if files else None
    for path in files:
        try:
            stat = path.stat()
            if path == newest or stat.st_mtime >= cutoff:
                continue
            size = stat.st_size
            path.unlink()
            removed += 1
            freed += size
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return removed, freed


def cleanup_cards(card_dir: Path, max_age_minutes: int | None = None, remove_all: bool = False) -> tuple[int, int]:
    age = int(os.getenv("CARD_RETENTION_MINUTES", "10")) if max_age_minutes is None else int(max_age_minutes)
    cutoff = time.time() - max(1, age) * 60.0
    removed = 0
    freed = 0
    if not card_dir.exists():
        return removed, freed
    for path in card_dir.glob("*.png"):
        try:
            stat = path.stat()
            if not remove_all and stat.st_mtime >= cutoff:
                continue
            size = stat.st_size
            path.unlink()
            removed += 1
            freed += size
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return removed, freed


def startup_cleanup() -> dict[str, int]:
    runtime = runtime_root()
    raw_dir = Path(os.getenv("RAW_LIVE_DIR", str(runtime / "raw" / "live")))
    analysis = Path(os.getenv("SIGNAL_ANALYSIS_PATH", str(runtime / "live" / "gool_bot2_analysis.jsonl")))
    shadow_analysis = Path(os.getenv("SHADOW_MARKET_ANALYSIS", str(runtime / "live" / "gool_bot2_shadow_analysis.jsonl")))
    cards = Path(os.getenv("SHADOW_MARKET_CARDS", str(runtime / "live" / "shadow_cards")))
    store = PrematchStore()

    result = {"freed_bytes": 0, "archives": 0, "raw_files": 0, "cards": 0, "prematch_saved": 0, "prematch_pruned": 0}
    result["prematch_saved"] = bootstrap_prematch_from_raw(raw_dir, store)

    count, freed = purge_rotated_live_archives(runtime)
    result["archives"] += count
    result["freed_bytes"] += freed

    raw_keep = int(os.getenv("RAW_LIVE_STARTUP_MAX_BYTES", str(24 * 1024 * 1024)))
    if raw_dir.exists():
        for path in raw_dir.glob("*.jsonl"):
            result["freed_bytes"] += trim_file_tail(path, raw_keep)
    count, freed = cleanup_raw_files(raw_dir)
    result["raw_files"] += count
    result["freed_bytes"] += freed

    analysis_keep = int(os.getenv("ANALYSIS_STARTUP_KEEP_BYTES", str(4 * 1024 * 1024)))
    for path in (analysis, shadow_analysis):
        result["freed_bytes"] += trim_file_tail(path, analysis_keep)

    count, freed = cleanup_cards(cards, remove_all=True)
    result["cards"] += count
    result["freed_bytes"] += freed

    count, freed = store.prune()
    result["prematch_pruned"] += count
    result["freed_bytes"] += freed
    return result


def runtime_cleanup() -> dict[str, int]:
    runtime = runtime_root()
    raw_dir = Path(os.getenv("RAW_LIVE_DIR", str(runtime / "raw" / "live")))
    cards = Path(os.getenv("SHADOW_MARKET_CARDS", str(runtime / "live" / "shadow_cards")))
    store = PrematchStore()
    result = {"freed_bytes": 0, "raw_files": 0, "cards": 0, "prematch_pruned": 0}
    count, freed = cleanup_raw_files(raw_dir)
    result["raw_files"] = count
    result["freed_bytes"] += freed
    count, freed = cleanup_cards(cards)
    result["cards"] = count
    result["freed_bytes"] += freed
    count, freed = store.prune()
    result["prematch_pruned"] = count
    result["freed_bytes"] += freed
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="GOOL runtime storage housekeeping")
    parser.add_argument("--once", action="store_true")
    parser.parse_args()
    result = startup_cleanup()
    print("GOOL_STORAGE_CLEANUP " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
