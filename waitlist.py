"""候診資料的純邏輯：數字轉換、解析表格列、找診間、判斷是否提醒。

不依賴 tkinter / selenium，可直接用 pytest 測試。
"""
import math
import re
import unicodedata
from typing import NamedTuple, Optional

import roman
from word2number import w2n

CHINESE_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000}
_CHINESE_RE = re.compile("[" + "".join(CHINESE_DIGITS) + "".join(CHINESE_UNITS) + "]+")
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")


class ClinicRow(NamedTuple):
    clinic: Optional[int]     # 診間號碼（解析失敗為 None）
    doctor: str
    time_slot: str
    sequence: Optional[int]   # 目前叫到的門診序（未開診 / "----" 為 None）
    waiting: Optional[int]    # 等待人數
    raw: tuple                # 網頁上的原始文字，用於顯示


def chinese_to_int(text: str) -> Optional[int]:
    """中文數字轉整數，例如「十一」→11、「一百零五」→105。無法解析回傳 None。"""
    if not text:
        return None
    result = 0   # 已完成的「萬」以上部分
    section = 0  # 萬以下的部分
    digit = None
    for ch in text:
        if ch in CHINESE_DIGITS:
            digit = CHINESE_DIGITS[ch]
        elif ch in CHINESE_UNITS:
            unit = CHINESE_UNITS[ch]
            if unit == 10000:
                section += digit or 0
                result += (section or 1) * unit
                section = 0
            else:
                section += (1 if digit is None else digit) * unit
            digit = None
        else:
            return None
    return result + section + (digit or 0)


def convert_to_int(text: str) -> Optional[int]:
    """把各種形式的數字轉為整數：阿拉伯／全形數字、羅馬數字、英文、中文。

    無法解析時回傳 None（例如「休診」、「----」、空字串）。
    """
    if text is None:
        return None
    text = unicodedata.normalize("NFKC", text).strip()  # 全形 → 半形
    if not text:
        return None

    num_match = re.search(r"\d+", text)
    if num_match:
        return int(num_match.group())

    upper = text.upper()
    if _ROMAN_RE.match(upper):
        try:
            return roman.fromRoman(upper)
        except roman.InvalidRomanNumeralError:
            pass

    if re.search(r"[A-Za-z]", text):
        try:
            return w2n.word_to_num(text.lower())
        except ValueError:
            pass

    chinese_match = _CHINESE_RE.search(text)
    if chinese_match:
        return chinese_to_int(chinese_match.group())
    return None


def parse_row(cells) -> ClinicRow:
    """把網頁一列的 5 個欄位文字轉為 ClinicRow。"""
    clinic_name, doctor, time_slot, sequence, waiting = (c.strip() for c in cells)
    return ClinicRow(
        clinic=convert_to_int(clinic_name),
        doctor=doctor,
        time_slot=time_slot,
        sequence=convert_to_int(sequence),
        waiting=convert_to_int(waiting),
        raw=(clinic_name, doctor, time_slot, sequence, waiting),
    )


def find_clinic(rows, clinic_no: int) -> Optional[ClinicRow]:
    """依診間號碼找資料列（不是用列的順序）。

    同一診間有多列時，優先回傳目前有叫號的那一列。找不到回傳 None。
    """
    matches = [r for r in rows if r.clinic == clinic_no]
    if not matches:
        return None
    for r in matches:
        if r.sequence is not None:
            return r
    return matches[0]


EMPTY = "—"


def _clean_text(text: str) -> str:
    text = " ".join(unicodedata.normalize("NFKC", text or "").split())
    return EMPTY if not text or set(text) <= {"-"} else text


def display_values(row: ClinicRow) -> tuple:
    """表格顯示用：全形轉半形、去掉號碼前面的 0、「----」改為「—」。"""
    clinic_name, doctor, time_slot, _, _ = row.raw
    return (
        _clean_text(clinic_name),
        _clean_text(doctor),
        _clean_text(time_slot),
        EMPTY if row.sequence is None else str(row.sequence),
        EMPTY if row.waiting is None else str(row.waiting),
    )


def progress_fraction(start_ahead: int, ahead: int, threshold: int) -> float:
    """從開始監控（前面 start_ahead 號）到提醒（前面 threshold 號）的進度，0～1。"""
    if start_ahead <= threshold:
        return 1.0
    return min(max((start_ahead - ahead) / (start_ahead - threshold), 0.0), 1.0)


def patients_ahead_threshold(minutes: int, minutes_per_patient: int = 5) -> int:
    """提前 minutes 分鐘提醒，相當於前面剩幾號時提醒。"""
    return math.ceil(minutes / minutes_per_patient)


DEFAULT_MINUTES_PER_PATIENT = 5


class PaceEstimator:
    """依觀察到的叫號速度估算「每號約幾分鐘」。

    只用最近 window_minutes 內的紀錄，且號碼至少前進 min_advance 號、
    觀察時間至少 min_span_minutes 分鐘才採用（避免一次跳過好幾個未到號時誤判為很快）。
    估計值會限制在 [min_pace, max_pace]：max_pace 預設等於原本的 5 分鐘，
    因此提醒只會比原本早、不會比原本晚（醫師休息時速度變慢也不會延後提醒）。
    """

    def __init__(self, max_pace: float = DEFAULT_MINUTES_PER_PATIENT, min_pace: float = 1.0,
                 window_minutes: float = 60, min_advance: int = 2, min_span_minutes: float = 10):
        self.max_pace = max_pace
        self.min_pace = min_pace
        self.window_seconds = window_minutes * 60
        self.min_advance = min_advance
        self.min_span_seconds = min_span_minutes * 60
        self._history = []  # [(timestamp_seconds, sequence)]

    def add(self, timestamp: float, sequence: Optional[int]):
        if sequence is None:
            return
        if self._history and sequence < self._history[-1][1]:
            self._history.clear()  # 號碼倒退：換時段或重新開始
        if self._history and sequence == self._history[-1][1]:
            return  # 號碼沒變，保留第一次看到該號的時間
        self._history.append((timestamp, sequence))
        cutoff = timestamp - self.window_seconds
        while len(self._history) > 2 and self._history[0][0] < cutoff:
            self._history.pop(0)

    def estimate(self) -> Optional[float]:
        """回傳估計的每號分鐘數；資料不足時回傳 None。"""
        if len(self._history) < 2:
            return None
        (t0, s0), (t1, s1) = self._history[0], self._history[-1]
        advanced = s1 - s0
        if advanced < self.min_advance or t1 - t0 < self.min_span_seconds:
            return None
        return (t1 - t0) / 60 / advanced

    def minutes_per_patient(self) -> float:
        """提醒判斷用的每號分鐘數：有估計值就用（限制在範圍內），否則用預設值。"""
        est = self.estimate()
        if est is None:
            return self.max_pace
        return min(max(est, self.min_pace), self.max_pace)


class Status(NamedTuple):
    kind: str                      # "not_found" | "not_open" | "passed" | "alarm" | "wait"
    row: Optional[ClinicRow] = None
    ahead: Optional[int] = None    # 前面還有幾號


def evaluate(rows, clinic: int, my_sequence: int, minutes: int,
             first_check: bool = False, minutes_per_patient: int = 5) -> Status:
    """每次抓到資料後決定下一步。

    - not_found：此院沒有該診間 → 停止監控
    - not_open ：診間尚未開診 → 繼續等
    - passed   ：開始監控時就已過號 → 停止監控
    - alarm    ：進入提醒範圍（監控途中過號也算）→ 提醒並停止
    - wait     ：繼續等
    """
    row = find_clinic(rows, clinic)
    if row is None:
        return Status("not_found")
    if row.sequence is None:
        return Status("not_open", row)
    ahead = my_sequence - row.sequence
    if first_check and ahead < 0:
        return Status("passed", row, ahead)
    if should_alarm(my_sequence, row.sequence, minutes, minutes_per_patient):
        return Status("alarm", row, ahead)
    return Status("wait", row, ahead)


def should_alarm(my_sequence: int, current: Optional[int], minutes: int,
                 minutes_per_patient: int = 5) -> bool:
    """目前叫號 current 與自己的號碼相差在提醒範圍內時回傳 True。未開診（None）不提醒。"""
    if current is None:
        return False
    return my_sequence - current <= patients_ahead_threshold(minutes, minutes_per_patient)
