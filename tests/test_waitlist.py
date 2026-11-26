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
