import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import messagebox, ttk
from types import SimpleNamespace

from fetcher import FetchError, WaitlistFetcher
from waitlist import convert_to_int, evaluate, parse_row

POLL_INTERVAL_MS = 30_000      # 每 30 秒更新一次
RESULT_CHECK_MS = 200          # 檢查背景抓取是否完成的間隔
MAX_SILENT_FAILURES = 3        # 連續失敗幾次才跳出警告（之後仍持續重試）
ALARM_SOUND = Path(__file__).resolve().parent / "alarm.mp3"

try:
    import pygame
    pygame.mixer.init()
    SOUND_AVAILABLE = ALARM_SOUND.exists()
except Exception:  # 沒有音訊裝置等情況：改用系統提示音
    SOUND_AVAILABLE = False

# 所有 Selenium 操作都在這一個背景執行緒進行，GUI 只在主執行緒更新
executor = ThreadPoolExecutor(max_workers=1)
fetcher = WaitlistFetcher()

state = SimpleNamespace(
    running=False,
    job=None,          # root.after 的排程 id
    future=None,       # 目前進行中的抓取
    params=None,       # (url, clinic, sequence, minutes)
    failures=0,
    first_check=True,
    rows=[],
)


def read_inputs():
    """讀取並驗證使用者輸入，回傳 (url, clinic, sequence, minutes)；不合法時跳警告並回傳 None。"""
    url = url_entry.get().strip()
    clinic = convert_to_int(clinic_entry.get())
    sequence = convert_to_int(sequence_entry.get())
    minutes = convert_to_int(alarm_time.get())
    if not url:
        messagebox.showwarning("輸入錯誤", "請輸入網址")
        return None
    if clinic is None or clinic <= 0:
        messagebox.showwarning("輸入錯誤", "請輸入正確的診間（例如 1、01、第一）")
        return None
    if sequence is None or sequence <= 0:
        messagebox.showwarning("輸入錯誤", "請輸入正確的門診序號（例如 15）")
        return None
    if minutes is None or minutes < 0:
        messagebox.showwarning("輸入錯誤", "請輸入提前幾分鐘提醒（例如 30）")
        return None
    return url, clinic, sequence, minutes


def set_status(text):
    status_label.config(text=text)


# ---------- 監控流程（全部在主執行緒） ----------

def start_monitoring():
    if state.running:
        return
    params = read_inputs()
    if params is None:
        return
    state.params = params
    state.running = True
    state.failures = 0
    state.first_check = True
    for w in (url_entry, clinic_entry, sequence_entry, alarm_time):
        w.config(state="disabled")
    fetch_button.config(state="disabled")
    stop_button.config(state="normal")
    poll()


def stop_monitoring(status=None):
    state.running = False
    if state.job is not None:
        root.after_cancel(state.job)
        state.job = None
    state.future = None  # 進行中的抓取結果會被忽略
    for w in (url_entry, clinic_entry, sequence_entry, alarm_time):
        w.config(state="normal")
    fetch_button.config(state="normal")
    stop_button.config(state="disabled")
    if status:
        set_status(status)


def schedule_next():
    state.job = root.after(POLL_INTERVAL_MS, poll)


def poll():
    state.job = None
    if not state.running:
        return
    set_status("更新中…")
    future = executor.submit(fetcher.fetch, state.params[0])
    state.future = future
    root.after(RESULT_CHECK_MS, wait_result, future)


def wait_result(future):
    if future is not state.future:  # 已停止或已重新開始，忽略舊結果
        return
    if not future.done():
        root.after(RESULT_CHECK_MS, wait_result, future)
        return
    try:
        data = future.result()
    except FetchError as e:
        on_fetch_failed(str(e))
        return
    except Exception as e:
        on_fetch_failed(f"未預期的錯誤：{e}")
        return
    on_data(data)


def on_fetch_failed(message):
    state.failures += 1
    set_status(f"⚠ 抓取失敗（連續 {state.failures} 次）：{message}\n{POLL_INTERVAL_MS // 1000} 秒後重試")
    schedule_next()
    if state.failures == MAX_SILENT_FAILURES:
        messagebox.showwarning(
            "警告",
            f"已連續 {state.failures} 次無法取得候診資料，程式會繼續重試。\n"
            f"請確認網路連線與網址是否正確。\n\n最後的錯誤：{message}",
        )


def on_data(data):
    state.failures = 0
    rows = [parse_row(r) for r in data]
    update_table(rows)

    _, clinic, sequence, minutes = state.params
    status = evaluate(rows, clinic, sequence, minutes, first_check=state.first_check)
    state.first_check = False
    now = time.strftime("%H:%M:%S")

    if status.kind == "not_found":
        available = "、".join(str(r.clinic) for r in rows if r.clinic is not None)
        stop_monitoring(f"找不到第 {clinic} 診，已停止監控")
        messagebox.showwarning("錯誤", f"找不到第 {clinic} 診（此院診間：{available or '無'}）")
    elif status.kind == "passed":
        stop_monitoring(f"目前已叫到 {status.row.sequence} 號，您的 {sequence} 號已過號")
        messagebox.showwarning("錯誤", "您的門診序已過號")
    elif status.kind == "not_open":
        set_status(f"[{now}] 第 {clinic} 診尚未開診，持續等待中")
        schedule_next()
    elif status.kind == "alarm":
        stop_monitoring(f"[{now}] 目前 {status.row.sequence} 號，快輪到您（{sequence} 號）了！")
        trigger_alarm()
    else:  # wait
        set_status(f"[{now}] 目前 {status.row.sequence} 號，您是 {sequence} 號，前面還有 {status.ahead} 號")
        schedule_next()


def update_table(rows):
    if rows == state.rows:
        return
    for item in result_table.get_children():
        result_table.delete(item)
    for row in rows:
        result_table.insert("", "end", values=row.raw)
    state.rows = rows


# ---------- 鬧鐘 ----------

def trigger_alarm():
    if SOUND_AVAILABLE:
        pygame.mixer.music.load(str(ALARM_SOUND))
        pygame.mixer.music.play(loops=-1)  # 無限循環播放，直到按下關閉
    else:
        root.bell()

    window_width, window_height = 250, 100
    x = root.winfo_x() + (root.winfo_width() - window_width) // 2
    y = root.winfo_y() + (root.winfo_height() - window_height) // 2
    alarm_window = tk.Toplevel(root)
    alarm_window.title("🔔 鬧鐘提醒")
    alarm_window.geometry(f"{window_width}x{window_height}+{x}+{y}")
    alarm_window.attributes("-topmost", True)
    tk.Label(alarm_window, text="快到您看病了喔").pack(pady=10)
    tk.Button(alarm_window, text="關閉鬧鐘", command=lambda: close_alarm(alarm_window)).pack(pady=10)
    alarm_window.protocol("WM_DELETE_WINDOW", lambda: close_alarm(alarm_window))


def close_alarm(alarm_window):
    if SOUND_AVAILABLE:
        pygame.mixer.music.stop()
    alarm_window.destroy()


def on_close():
    stop_monitoring()
    executor.submit(fetcher.close)  # 排在進行中的抓取之後，確保 Chrome 被關閉
    executor.shutdown(wait=False)
    root.destroy()


# ---------- 介面 ----------

root = tk.Tk()
root.title("自動雲端候診提醒")

tk.Label(root, text="網址：").grid(row=0, column=0, padx=10, pady=10)
url_entry = tk.Entry(root, width=50)
url_entry.grid(row=0, column=1, padx=10, pady=10)

tk.Label(root, text="診間：").grid(row=1, column=0, padx=10, pady=10)
clinic_entry = tk.Entry(root, width=50)
clinic_entry.grid(row=1, column=1, padx=10, pady=10)

tk.Label(root, text="您的門診序：").grid(row=2, column=0, padx=10, pady=10)
sequence_entry = tk.Entry(root, width=50)
sequence_entry.grid(row=2, column=1, padx=10, pady=10)

tk.Label(root, text="⏰ 幾分鐘前提醒您：").grid(row=3, column=0, padx=10, pady=10)
alarm_time = tk.Entry(root, width=50)
alarm_time.grid(row=3, column=1, padx=10, pady=10)

# 按 Enter 移到下一欄；在最後一欄按 Enter 開始監控
url_entry.bind("<Return>", lambda e: clinic_entry.focus())
clinic_entry.bind("<Return>", lambda e: sequence_entry.focus())
sequence_entry.bind("<Return>", lambda e: alarm_time.focus())
alarm_time.bind("<Return>", lambda e: start_monitoring())

button_frame = tk.Frame(root)
button_frame.grid(row=4, column=1, pady=10)
fetch_button = tk.Button(button_frame, text="開始監控 (自動更新)", command=start_monitoring)
fetch_button.pack(side="left", padx=5)
stop_button = tk.Button(button_frame, text="停止監控", state="disabled",
                        command=lambda: stop_monitoring("已停止監控"))
stop_button.pack(side="left", padx=5)

status_label = tk.Label(root, text="請輸入資料後按「開始監控」", justify="left", anchor="w")
status_label.grid(row=5, column=0, columnspan=2, padx=10, sticky="w")

columns = ("診間名稱", "看診醫師", "時段", "門診序", "等待")
result_table = ttk.Treeview(root, columns=columns, show="headings")
for col in columns:
    result_table.heading(col, text=col)
    result_table.column(col, width=100)
result_table.grid(row=6, column=0, columnspan=2, padx=10, pady=10)

root.protocol("WM_DELETE_WINDOW", on_close)

if __name__ == "__main__":
    root.mainloop()
