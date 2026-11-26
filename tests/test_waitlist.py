import pytest

from waitlist import (
    ClinicRow, chinese_to_int, convert_to_int, find_clinic,
    parse_row, patients_ahead_threshold, should_alarm,
)


@pytest.mark.parametrize("text, expected", [
    ("1", 1), ("01", 1), ("１２", 12), ("０１ 診", 1), ("0013", 13), ("15號", 15),
    ("第一診", 1), ("第十一診", 11), ("二十", 20), ("一百零五", 105), ("兩", 2),
    ("IV", 4), ("iv", 4), ("two", 2), ("Twenty one", 21),
])
def test_convert_to_int_valid(text, expected):
    assert convert_to_int(text) == expected


@pytest.mark.parametrize("text", ["", "   ", None, "休診", "----", "--", "未開診", "載入中......", "Civil"])
def test_convert_to_int_invalid_returns_none(text):
    assert convert_to_int(text) is None


@pytest.mark.parametrize("text, expected", [
    ("零", 0), ("十", 10), ("十一", 11), ("二十一", 21), ("一百", 100),
    ("一千二百", 1200), ("一萬二千", 12000), ("十萬", 100000),
])
def test_chinese_to_int(text, expected):
    assert chinese_to_int(text) == expected


def test_parse_row_real_site_format():
    # 實際網頁抓到的格式（2026-11-26）
    row = parse_row(["０１ 診", "王棋祺 醫師", "下午", "0013", "3"])
    assert row.clinic == 1
    assert row.sequence == 13
    assert row.waiting == 3
    assert row.raw == ("０１ 診", "王棋祺 醫師", "下午", "0013", "3")


def test_parse_row_closed_clinic():
    row = parse_row(["０２ 診", "----", "----", "----", "----"])
    assert row.clinic == 2
    assert row.sequence is None
    assert row.waiting is None


def _row(clinic, seq):
    return ClinicRow(clinic, "", "", seq, None, ())


def test_find_clinic_by_number_not_position():
    rows = [_row(2, 10), _row(5, 20), _row(12, 30)]
    assert find_clinic(rows, 5).sequence == 20
    assert find_clinic(rows, 12).sequence == 30
    assert find_clinic(rows, 1) is None
    assert find_clinic(rows, 0) is None
    assert find_clinic([], 1) is None


def test_find_clinic_prefers_active_row():
    rows = [_row(3, None), _row(3, 8)]
    assert find_clinic(rows, 3).sequence == 8
    assert find_clinic([_row(3, None)], 3).sequence is None


@pytest.mark.parametrize("minutes, expected", [(0, 0), (1, 1), (5, 1), (6, 2), (30, 6)])
def test_patients_ahead_threshold(minutes, expected):
    assert patients_ahead_threshold(minutes) == expected


def test_should_alarm():
    # 30 分鐘 → 前面剩 6 號內提醒
    assert should_alarm(20, 14, 30) is True
    assert should_alarm(20, 13, 30) is False
    assert should_alarm(20, 25, 30) is True    # 已過號也提醒
    assert should_alarm(20, None, 30) is False  # 未開診不提醒


def test_evaluate():
    from waitlist import evaluate
    rows = [_row(1, 13), _row(2, None)]
    assert evaluate(rows, 9, 20, 30).kind == "not_found"
    assert evaluate(rows, 2, 20, 30).kind == "not_open"
    assert evaluate(rows, 1, 10, 30, first_check=True).kind == "passed"
    assert evaluate(rows, 1, 10, 30).kind == "alarm"     # 監控途中過號 → 提醒
    s = evaluate(rows, 1, 19, 30)                          # 前面 6 號、30 分鐘 → 提醒
    assert (s.kind, s.ahead) == ("alarm", 6)
    s = evaluate(rows, 1, 50, 30)
    assert (s.kind, s.ahead, s.row.sequence) == ("wait", 37, 13)


# ---------- PaceEstimator ----------
from waitlist import PaceEstimator

MIN = 60


def test_pace_no_data_uses_default():
    p = PaceEstimator()
    assert p.estimate() is None
    assert p.minutes_per_patient() == 5
    p.add(0, 10)
    p.add(10 * MIN, 11)          # 只前進 1 號，不足以估計
    assert p.estimate() is None
    assert p.minutes_per_patient() == 5


def test_pace_fast_clinic_estimated():
    p = PaceEstimator()
    p.add(0, 10)
    p.add(5 * MIN, 12)
    p.add(9 * MIN, 13)           # 9 分鐘 3 號 → 每號 3 分鐘
    assert p.estimate() == pytest.approx(3)
    assert p.minutes_per_patient() == pytest.approx(3)


def test_pace_slow_clinic_capped_at_default():
    p = PaceEstimator()
    p.add(0, 10)
    p.add(30 * MIN, 12)          # 每號 15 分鐘 → 上限 5，不延後提醒
    assert p.estimate() == pytest.approx(15)
    assert p.minutes_per_patient() == 5


def test_pace_floor():
    p = PaceEstimator()
    p.add(0, 10)
    p.add(1 * MIN, 20)           # 每號 0.1 分鐘 → 下限 1
    assert p.minutes_per_patient() == 1


def test_pace_unchanged_number_keeps_first_seen_time():
    p = PaceEstimator()
    p.add(0, 10)
    p.add(4 * MIN, 10)
    p.add(8 * MIN, 10)
    p.add(8 * MIN + 1, 12)       # 10 號從 0 分開始 → 8 分鐘 2 號 = 4 分/號
    assert p.estimate() == pytest.approx(4, abs=0.01)


def test_pace_resets_when_number_goes_back():
    p = PaceEstimator()
    p.add(0, 30)
    p.add(10 * MIN, 35)
    p.add(20 * MIN, 1)           # 換時段
    assert p.estimate() is None
    p.add(26 * MIN, 3)
    assert p.estimate() == pytest.approx(3)


def test_pace_ignores_none_and_old_history():
    p = PaceEstimator(window_minutes=60)
    p.add(0, 1)
    p.add(5 * MIN, None)
    p.add(10 * MIN, 2)
    p.add(100 * MIN, 30)
    p.add(110 * MIN, 34)         # 舊紀錄被丟掉，只看 100~110 分：10 分鐘 4 號
    assert p.estimate() == pytest.approx(2.5)


def test_evaluate_with_faster_pace_alarms_earlier():
    from waitlist import evaluate
    rows = [_row(1, 10)]
    # 前面 8 號、提前 30 分鐘：每號 5 分 → 門檻 6 號，不提醒；每號 3 分 → 門檻 10 號，提醒
    assert evaluate(rows, 1, 18, 30, minutes_per_patient=5).kind == "wait"
    assert evaluate(rows, 1, 18, 30, minutes_per_patient=3).kind == "alarm"
