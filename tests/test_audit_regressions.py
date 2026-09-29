from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gool_bot2 import v4_prematch_delivery as delivery
from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.multi_delivery import pending_result_notifications
from gool_bot2.multi_journal import settle_multi_journal
from gool_bot2.v4_prematch_engine import PrematchPick
from gool_bot2.v4_prematch_settlement import settle_prematch_pick, sync_prematch_journal


def pick(event='a', market='FT_OVER_2.5'):
    return PrematchPick(event, 'Home '+event, 'Away '+event, market, 'over', 1.8, .8, .55)


def record(event='a', score=(2, 1), final=True, halftime=False):
    return {'match': {'flashscore_event_id': event, 'home': 'Home '+event, 'away': 'Away '+event,
                      'home_score': score[0], 'away_score': score[1], 'minute': 90 if final else 45 if halftime else 20,
                      'is_finished': final, 'is_halftime': halftime}}


@pytest.fixture
def fake_telegram(monkeypatch):
    calls = []
    monkeypatch.setattr(delivery, 'render_v4_prematch_card', lambda *a, **k: b'png')
    monkeypatch.setattr(delivery, 'render_v4_parlay_card', lambda *a, **k: b'png')
    monkeypatch.setattr(delivery.telegram, 'broadcast_photo', lambda *a, **k: calls.append(k) or 1)
    return calls


def test_single_sent_once_under_concurrent_retries(tmp_path, fake_telegram):
    path = tmp_path/'journal.json'
    row = delivery.prematch_row_from_pick(pick())
    with ThreadPoolExecutor(max_workers=5) as pool:
        sent = list(pool.map(lambda _: delivery.emit_prematch_signal(row, path), range(5)))
    assert sum(sent) == 1
    assert len(fake_telegram) == 1
    assert len(load_signal_journal(path)) == 1


def test_unsent_single_can_retry(tmp_path, fake_telegram, monkeypatch):
    path = tmp_path/'journal.json'
    row = delivery.prematch_row_from_pick(pick())
    send = delivery.telegram.broadcast_photo
    monkeypatch.setattr(delivery.telegram, 'broadcast_photo', lambda *a, **k: 0)
    assert delivery.emit_prematch_signal(row, path) == 0
    monkeypatch.setattr(delivery.telegram, 'broadcast_photo', send)
    assert delivery.emit_prematch_signal(row, path) == 1
    assert delivery.emit_prematch_signal(row, path) == 0


def test_parlay_retries_and_order_do_not_duplicate(tmp_path, fake_telegram, monkeypatch):
    path = tmp_path/'journal.json'
    acc = {'legs': [pick('a'), pick('b')], 'combined_odds': 3.24}
    payload = {'doubles': [acc]}
    send = delivery.telegram.broadcast_photo
    monkeypatch.setattr(delivery.telegram, 'broadcast_photo', lambda *a, **k: 0)
    assert delivery.emit_delivery_selection(payload, {}, path)['cards'] == 0
    monkeypatch.setattr(delivery.telegram, 'broadcast_photo', send)
    assert delivery.emit_delivery_selection(payload, {}, path)['cards'] == 1
    acc['legs'].reverse()
    assert delivery.emit_delivery_selection(payload, {}, path)['cards'] == 0
    assert len(load_signal_journal(path)) == 1


def test_prematch_under_cannot_win_while_match_is_live(tmp_path):
    path = tmp_path/'journal.json'
    row = delivery.prematch_row_from_pick(pick(market='FT_UNDER_2.5'))
    row['telegram_sent'] = True
    save_signal_journal(path, [row])
    live = record(score=(0, 0), final=False)
    settle_multi_journal(live, path)
    settle_multi_journal(live, path)
    assert load_signal_journal(path)[0]['result'] == 'pending'


@pytest.mark.parametrize('market,score,ht,expected', [
    ('BTTS_YES', (2, 1), None, 'won'),
    ('BTTS_YES', (2, 0), None, 'lost'),
    ('BTTS_NO', (2, 0), None, 'won'),
    ('1H_OVER_0.5', (3, 1), (0, 0), 'lost'),
    ('1H_OVER_0.5', (3, 1), (1, 0), 'won'),
    ('1H_OVER_1.5', (3, 1), (1, 0), 'lost'),
    ('1H_OVER_0.5', (3, 1), None, None),
    ('2H_OVER_1.5', (3, 1), (2, 1), 'lost'),
    ('2H_OVER_1.5', (3, 1), (1, 0), 'won'),
    ('2H_OVER_1.5', (3, 1), (4, 1), None),
])
def test_market_period_and_btts_semantics(market, score, ht, expected):
    assert settle_prematch_pick({'market': market}, *score, half_time_score=ht) == expected


def test_parlay_legs_settle_without_individual_singles(tmp_path):
    path = tmp_path/'journal.json'
    parent = {'entry_id': 'parlay:test', 'origin': 'prematch_parlay', 'result': 'pending',
              'telegram_sent': True, 'odd': 3.24,
              'legs': [delivery.prematch_row_from_pick(pick('a')), delivery.prematch_row_from_pick(pick('b'))]}
    save_signal_journal(path, [parent])
    sync_prematch_journal(path, record('a'))
    assert load_signal_journal(path)[0]['result'] == 'pending'
    sync_prematch_journal(path, record('b'))
    stored = load_signal_journal(path)[0]
    assert stored['result'] == 'won'
    assert stored['effective_odd'] == 3.24
    assert len(pending_result_notifications(path, match_id='b')) == 1
    assert pending_result_notifications(path, match_id='b') == []


def test_first_half_settles_at_break_and_is_not_reopened(tmp_path):
    path = tmp_path/'journal.json'
    row = delivery.prematch_row_from_pick(pick(market='1H_OVER_0.5'))
    save_signal_journal(path, [row])
    sync_prematch_journal(path, record(score=(1, 0), final=False, halftime=True))
    stored = load_signal_journal(path)[0]
    assert (stored['result'], stored['settled_minute'], stored['settled_score']) == ('won', 45, [1, 0])
    sync_prematch_journal(path, record(score=(2, 1), final=False))
    assert load_signal_journal(path)[0]['lifecycle'] == 'settled'


def test_live_and_prematch_results_share_one_claim_pass(tmp_path, monkeypatch):
    from gool_bot2 import multi_runtime as runtime
    path = tmp_path/'journal.json'
    rows = [dict(entry_key='live:a', origin='live', match_id='a', result='won', telegram_sent=True,
                 result_notification_pending=True),
            dict(entry_id='prematch:a', origin='prematch', match_id='a', result='won', telegram_sent=True,
                 result_notification_pending=True)]
    save_signal_journal(path, rows)
    monkeypatch.setattr(runtime, '_paths', lambda: (tmp_path/'analysis.jsonl', path))
    monkeypatch.setattr(runtime, 'settle_multi_journal', lambda *a: [])
    received = []
    monkeypatch.setattr(runtime, 'emit_multi_results', lambda rec, rows, **kw: received.extend(rows))
    monkeypatch.setattr(runtime, 'emit_prematch_result', lambda *a: 1)
    runtime.observe_multi_shadow(SimpleNamespace(), record())
    assert [r['entry_key'] for r in received] == ['live:a']
    assert load_signal_journal(path)[1]['result_telegram_sent'] is True


def test_all_deployable_python_compiles():
    root = Path(__file__).resolve().parents[1]
    paths = [root/'monkey_start.py', root/'monkey_autoupdate.py']
    for folder in ('src', 'scripts'):
        paths.extend((root/folder).rglob('*.py'))
    for path in paths:
        compile(path.read_bytes(), str(path), 'exec')


def test_cumulative_pressure_without_recent_window_stays_no_bet():
    from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
    r = record(final=False)
    stats = {'xg': [1.4, .8], 'shots': [10, 7], 'shots_on_target': [5, 3], 'big_chances': [3, 1]}
    r['providers'] = {'flashscore': {'stats': stats}, 'fotmob': {'stats': stats}}
    assert all(d.decision == 'NO_BET' for d in evaluate_live_goals(r))
    r['live_momentum'] = {'minutes_in_epoch': 5, 'xg_total_last_5m': 0, 'shots_total_last_5m': 0}
    assert all(d.decision == 'NO_BET' for d in evaluate_live_goals(r))


def test_old_prematch_signal_with_fresh_settlement_is_not_suppressed(tmp_path):
    path = tmp_path / "journal.json"
    now = datetime.now(timezone.utc)
    row = {
        "entry_id":"prematch:old-signal","origin":"prematch","result":"won","telegram_sent":True,
        "created_at":(now - timedelta(hours=10)).isoformat(),
        "settled_at":now.isoformat(),"result_notification_created_at":now.isoformat(),
        "result_notification_pending":True,
    }
    save_signal_journal(path, [row])
    claimed = pending_result_notifications(path, origins={"prematch"})
    assert len(claimed) == 1
    assert claimed[0]["entry_id"] == "prematch:old-signal"
