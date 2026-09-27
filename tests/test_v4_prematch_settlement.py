from gool_bot2.v4_prematch_settlement import settle_parlay, settle_prematch_pick, settle_prematch_row


def test_1x2_home_draw_away():
    assert settle_prematch_pick({"market_family":"match_1x2","selection":"home"},2,1) == "won"
    assert settle_prematch_pick({"market_family":"match_1x2","selection":"draw"},1,1) == "won"
    assert settle_prematch_pick({"market_family":"match_1x2","selection":"away"},0,2) == "won"
    assert settle_prematch_pick({"market_family":"match_1x2","selection":"home"},0,1) == "lost"


def test_totals_and_integer_push():
    assert settle_prematch_pick({"market":"ТБ 2.5"},2,1) == "won"
    assert settle_prematch_pick({"market":"ТМ 2.5"},2,1) == "lost"
    assert settle_prematch_pick({"market":"ТБ 3.0"},2,1) == "push"


def test_final_record_settles_same_prematch_row():
    row={"origin":"prematch","result":"pending","match_id":"fs1","market":"ТБ 2.5","odd":1.8}
    record={"match":{"flashscore_event_id":"fs1","home_score":2,"away_score":1,"is_finished":True}}
    assert settle_prematch_row(row,record)
    assert row["result"]=="won"
    assert row["settled_score"]==[2,1]
    assert row["result_notification_pending"] is True


def test_parlay_loses_when_one_leg_loses():
    row={"origin":"prematch_parlay","result":"pending","odd":3.0,"legs":[
        {"event_id":"a","result":"won","odd":1.8},
        {"event_id":"b","result":"lost","odd":1.7},
    ]}
    assert settle_parlay(row)
    assert row["result"]=="lost"


def test_parlay_push_leg_reduces_effective_odds():
    row={"origin":"prematch_parlay","result":"pending","odd":3.06,"legs":[
        {"event_id":"a","result":"won","odd":1.8},
        {"event_id":"b","result":"push","odd":1.7},
    ]}
    assert settle_parlay(row)
    assert row["result"]=="won"
    assert row["effective_odd"]==1.8
