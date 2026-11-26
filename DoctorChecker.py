import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import messagebox, ttk
from types import SimpleNamespace

import settings
from fetcher import FetchError, WaitlistFetcher
from waitlist import (
    PaceEstimator, convert_to_int, display_values, evaluate, find_clinic, parse_row,
    patients_ahead_threshold, progress_fraction,
)

POLL_INTERVAL_MS = 30_000      # 每 30 秒更新一次
RESULT_CHECK_MS = 200          # 檢查背景抓取是否完成的間隔
MAX_SILENT_FAILURES = 3        # 連續失敗幾次才跳出警告（之後仍持續重試）
BASE_DIR = Path(__file__).resolve().parent
ALARM_SOUND = BASE_DIR / "alarm.mp3"
ICON_ICO = BASE_DIR / "assets" / "icon.ico"
ICON_PNG = BASE_DIR / "assets" / "icon.png"

# 配色（與圖示一致）
TEAL = "#0E8C8F"
TEAL_DARK = "#0B7A85"
TEAL_LIGHT = "#1FB5AC"
TEAL_BG = "#E1F5F3"
BG = "#F5F7F8"
CARD = "#FFFFFF"
BORDER = "#DDE3E7"
INPUT_BORDER = "#BCC6CE"
TEXT = "#1F2933"
MUTED = "#52606D"
HINT = "#9AA5B1"
GRAY = "#7B8794"
ORANGE = "#B26B00"
RED = "#C62F34"
RED_BG = "#E5484D"

STATUS_COLORS = {
    "idle": GRAY, "stopped": GRAY, "connecting": TEAL, "wait": TEAL,
    "not_open": ORANGE, "failing": ORANGE,
    "alarm": RED, "passed": RED, "not_found": RED,
}

try:
    import pygame
    pygame.mixer.init()
    SOUND_AVAILABLE = ALARM_SOUND.exists()
except Exception:  # 沒有音訊裝置等情況：改用系統提示音
    SOUND_AVAILABLE = False

try:
    from plyer import notification  # 選用：系統通知
except ImportError:
    notification = None

# 所有 Selenium 操作都在這一個背景執行緒進行，GUI 只在主執行緒更新
executor = ThreadPoolExecutor(max_workers=1)
fetcher = WaitlistFetcher()

state = SimpleNamespace(
    running=False,
    job=None,            # root.after 的排程 id
    future=None,         # 目前進行中的抓取
    params=None,         # (url, clinic, sequence, minutes)
    failures=0,
    first_check=True,
    rows=[],
    pace=PaceEstimator(),
    start_ahead=None,    # 開始監控時前面還有幾號（進度條起點）
    next_poll_at=None,   # 下次更新的時間（倒數用）
    updating=False,
    foot_note="",        # 狀態卡右下角的說明（倒數前綴）
    status_kind="idle",
)


# ---------- 輸入 ----------

FIELD_HINTS = {}  # entry -> (hint label, 預設提示文字)


def show_field_error(entry, message):
    label, _ = FIELD_HINTS[entry]
    label.config(text=message, style="Error.TLabel")
    entry.focus_set()


def clear_field_errors():
    for label, default in FIELD_HINTS.values():
        label.config(text=default, style="Hint.TLabel")


def read_inputs():
    """讀取並驗證輸入，回傳 (url, clinic, sequence, minutes)；不合法時在欄位下方顯示錯誤並回傳 None。"""
    clear_field_errors()
    url = url_entry.get().strip()
    clinic = convert_to_int(clinic_entry.get())
    sequence = convert_to_int(sequence_entry.get())
    minutes = convert_to_int(alarm_time.get())
    if not url:
        show_field_error(url_entry, "請輸入診所的候診網址")
    elif clinic is None or clinic <= 0:
        show_field_error(clinic_entry, "請輸入診間，例：1、01、第一")
    elif sequence is None or sequence <= 0:
        show_field_error(sequence_entry, "請輸入門診序號，例：15")
    elif minutes is None or minutes < 0:
        show_field_error(alarm_time, "請輸入分鐘數，例：30")
    else:
        return url, clinic, sequence, minutes
    return None


def set_inputs_enabled(enabled):
    for w in (url_entry, clinic_entry, sequence_entry, alarm_time):
        w.state(["!disabled"] if enabled else ["disabled"])
    fetch_button.state(["!disabled"] if enabled else ["disabled"])
    stop_button.state(["disabled"] if enabled else ["!disabled"])


# ---------- 狀態卡 ----------

def show_status(kind, title, *, number=None, facts=(), message="", progress=None,
                bar_left="", bar_right="", foot_left="", foot_note=""):
    """更新狀態卡。number 有值時顯示「N 號後輪到您」大字，否則顯示 title。"""
    state.status_kind = kind
    color = STATUS_COLORS.get(kind, GRAY)
    status_accent.config(bg=color)

    if number is not None:
        big_number.config(text=str(number), foreground=color)
        big_number.pack(side="left", anchor="s", before=big_text)
        big_text.config(text=title, foreground=TEXT, font=F_BIG_TEXT)
    else:
        big_number.pack_forget()
        big_text.config(text=title, foreground=color, font=F_TITLE)

    for child in facts_frame.winfo_children():
        child.destroy()
    if facts:
        for name, value, unit in facts:
            ttk.Label(facts_frame, text=name, style="CardMuted.TLabel").pack(side="left")
            ttk.Label(facts_frame, text=f" {value}", style="CardValue.TLabel").pack(side="left")
            ttk.Label(facts_frame, text=f" {unit}" if unit else "", style="CardMuted.TLabel").pack(side="left", padx=(0, 16))
    elif message:
        ttk.Label(facts_frame, text=message, style="CardMuted.TLabel", wraplength=470,
                  justify="left").pack(side="left")

    if progress is None:
        progress_frame.pack_forget()
    else:
        progress_bar.config(value=progress * 100)
        bar_left_label.config(text=bar_left)
        bar_right_label.config(text=bar_right)
        progress_frame.pack(fill="x", pady=(8, 0), before=foot_frame)

    foot_dot.config(foreground=color)
    foot_left_label.config(text=foot_left)
    state.foot_note = foot_note
    refresh_footer()


def refresh_footer():
    if state.updating:
        suffix = "更新中…"
    elif state.running and state.next_poll_at:
        suffix = f"{max(0, round(state.next_poll_at - time.time()))} 秒後更新"
    else:
        suffix = ""
    foot_right_label.config(text=" · ".join(p for p in (state.foot_note, suffix) if p))


def tick():
    refresh_footer()
    root.after(1000, tick)


# ---------- 監控流程（全部在主執行緒） ----------

def start_monitoring():
    if state.running:
        return
    params = read_inputs()
    if params is None:
        return
    state.params = params
    settings.save({"url": url_entry.get().strip(), "clinic": clinic_entry.get().strip(),
                   "minutes": alarm_time.get().strip()})
    state.running = True
    state.failures = 0
    state.first_check = True
    state.pace = PaceEstimator()
    state.start_ahead = None
    set_inputs_enabled(False)
    show_status("connecting", "正在取得候診資料…", message="第一次啟動瀏覽器可能需要幾秒鐘",
                foot_left="監控中")
    poll()


def stop_monitoring(status_title=None, message=""):
    state.running = False
    state.updating = False
    state.next_poll_at = None
    if state.job is not None:
        root.after_cancel(state.job)
        state.job = None
    state.future = None  # 進行中的抓取結果會被忽略
    set_inputs_enabled(True)
    if status_title:
        show_status("stopped", status_title, message=message, foot_left="已停止")


def schedule_next():
    state.next_poll_at = time.time() + POLL_INTERVAL_MS / 1000
    state.job = root.after(POLL_INTERVAL_MS, poll)


def poll():
    state.job = None
    if not state.running:
        return
    state.updating = True
    state.next_poll_at = None
    refresh_footer()
    future = executor.submit(fetcher.fetch, state.params[0])
    state.future = future
    root.after(RESULT_CHECK_MS, wait_result, future)


def wait_result(future):
    if future is not state.future:  # 已停止或已重新開始，忽略舊結果
        return
    if not future.done():
        root.after(RESULT_CHECK_MS, wait_result, future)
        return
    state.updating = False
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
    schedule_next()
    show_status("failing", "暫時無法取得候診資料", message=message,
                foot_left=f"連續失敗 {state.failures} 次，會自動重試")
    if state.failures == MAX_SILENT_FAILURES:
        messagebox.showwarning(
            "警告",
            f"已連續 {state.failures} 次無法取得候診資料，程式會繼續重試。\n"
            f"請確認網路連線與網址是否正確。\n\n最後的錯誤：{message}",
        )


def on_data(data):
    state.failures = 0
    rows = [parse_row(r) for r in data]
    _, clinic, sequence, minutes = state.params
    update_table(rows, clinic)

    row = find_clinic(rows, clinic)
    if row is not None:
        state.pace.add(time.time(), row.sequence)
    pace = state.pace.minutes_per_patient()
    status = evaluate(rows, clinic, sequence, minutes, first_check=state.first_check,
                      minutes_per_patient=pace)
    state.first_check = False
    now = time.strftime("%H:%M:%S")

    if status.kind == "not_found":
        available = "、".join(str(r.clinic) for r in rows if r.clinic is not None)
        stop_monitoring()
        show_status("not_found", f"找不到第 {clinic} 診",
                    message=f"這家診所的診間：{available or '無'}。請確認診間後重新開始。",
                    foot_left="已停止")
    elif status.kind == "passed":
        stop_monitoring()
        show_status("passed", "您的號碼已過號",
                    facts=(("目前叫號", status.row.sequence, ""), ("您的號碼", sequence, "")),
                    foot_left="已停止")
    elif status.kind == "not_open":
        schedule_next()
        show_status("not_open", "尚未開診", message=f"第 {clinic} 診目前沒有叫號，持續等待中",
                    foot_left=f"監控中 · 最後更新 {now}")
    elif status.kind == "alarm":
        stop_monitoring()
        show_status("alarm", "快輪到您了！",
                    facts=(("目前叫號", status.row.sequence, ""), ("您的號碼", sequence, ""),
                           ("前面還有", max(status.ahead, 0), "號")),
                    foot_left=f"已提醒 · {now}")
        trigger_alarm(clinic, status.row.sequence, sequence, status.ahead, pace)
    else:  # wait
        threshold = patients_ahead_threshold(minutes, pace)
        if state.start_ahead is None or status.ahead > state.start_ahead:
            state.start_ahead = status.ahead
        source = "依叫號速度估算" if state.pace.estimate() is not None else "預設值"
        schedule_next()
        show_status(
            "wait", "號後輪到您", number=status.ahead,
            facts=(("目前叫號", status.row.sequence, ""), ("您的號碼", sequence, ""),
                   ("預估約", round(status.ahead * pace), "分鐘")),
            progress=progress_fraction(state.start_ahead, status.ahead, threshold),
            bar_left=f"開始監控時 前面 {state.start_ahead} 號",
            bar_right=f"剩 {threshold} 號時提醒",
            foot_left=f"監控中 · 最後更新 {now}",
            foot_note=f"每號約 {pace:.1f} 分鐘（{source}）",
        )


def update_table(rows, my_clinic=None):
    state.rows = rows
    result_table.delete(*result_table.get_children())
    for row in rows:
        values = list(display_values(row))
        mine = my_clinic is not None and row.clinic == my_clinic
        if mine:
            values[0] += "  ★ 您的診間"
        result_table.insert("", "end", values=values, tags=("mine",) if mine else ())
    result_table.config(height=max(2, min(len(rows), 8)))


# ---------- 鬧鐘 ----------

def notify_system(message):
    """發送系統通知；未安裝 plyer 或失敗時略過。"""
    if notification is None:
        return
    try:
        notification.notify(title="自動雲端候診提醒", message=message,
                            app_name="自動雲端候診提醒", timeout=30)
    except Exception:
        pass


def bring_to_front():
    """還原最小化的主視窗、拉到最上層，並讓工作列圖示閃爍（Windows）。"""
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.after(1000, lambda: root.attributes("-topmost", False))
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                            ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                            ("dwTimeout", wintypes.DWORD)]

            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            FLASHW_ALL, FLASHW_TIMERNOFG = 0x3, 0xC  # 閃爍直到視窗取得焦點
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass


def rich_line(parent, parts, bg):
    """一行混合字型的文字：parts = [(text, font, color), ...]。"""
    line = tk.Frame(parent, bg=bg)
    for text, font, color in parts:
        tk.Label(line, text=text, font=font, fg=color, bg=bg).pack(side="left", anchor="s")
    line.pack(pady=1)
    return line


def trigger_alarm(clinic, current, sequence, ahead, pace):
    ahead = max(ahead, 0)
    message = f"第 {clinic} 診目前叫到 {current} 號，您是 {sequence} 號，前面還有 {ahead} 號"
    bring_to_front()
    notify_system(message)
    if SOUND_AVAILABLE:
        pygame.mixer.music.load(str(ALARM_SOUND))
        pygame.mixer.music.play(loops=-1)  # 無限循環播放，直到按下關閉
    else:
        root.bell()

    win = tk.Toplevel(root, bg=CARD)
    win.title("鬧鐘提醒")
    win.resizable(False, False)
    win.attributes("-topmost", True)

    head = tk.Frame(win, bg=RED_BG, padx=20, pady=16)
    head.pack(fill="x")
    if ICON_SMALL is not None:
        tk.Label(head, image=ICON_ALARM, bg=RED_BG).pack()
    tk.Label(head, text="快輪到您了！", font=F_ALARM_TITLE, fg="white", bg=RED_BG).pack(pady=(6, 0))

    body = tk.Frame(win, bg=BG, padx=24, pady=14)
    body.pack(fill="x")
    rich_line(body, [(f"第 {clinic} 診目前叫到 ", F_BASE_L, TEXT), (str(current), F_ALARM_NUM, RED),
                     (" 號", F_BASE_L, TEXT)], BG)
    rich_line(body, [("您是 ", F_BASE_L, TEXT), (str(sequence), F_ALARM_NUM, RED),
                     (" 號，前面還有 ", F_BASE_L, TEXT), (str(ahead), F_ALARM_NUM, RED),
                     (" 號", F_BASE_L, TEXT)], BG)
    tk.Label(body, text=f"預估約 {round(ahead * pace)} 分鐘，請準備前往診間",
             font=F_SMALL, fg=MUTED, bg=BG).pack(pady=(4, 0))

    act = tk.Frame(win, bg=BG, padx=20, pady=(0))
    act.pack(fill="x")
    close_btn = ttk.Button(act, text="我知道了（關閉鬧鐘）", style="Danger.TButton",
                           command=lambda: close_alarm(win))
    close_btn.pack(fill="x", pady=(0, 18))
    win.protocol("WM_DELETE_WINDOW", lambda: close_alarm(win))
    win.bind("<Return>", lambda e: close_alarm(win))
    win.bind("<Escape>", lambda e: close_alarm(win))

    win.update_idletasks()
    w, h = max(win.winfo_reqwidth(), 380), win.winfo_reqheight()
    x = root.winfo_x() + (root.winfo_width() - w) // 2
    y = root.winfo_y() + (root.winfo_height() - h) // 3
    win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")
    win.focus_force()
    close_btn.focus_set()


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

def set_app_icon(window):
    """設定視窗與工作列圖示；找不到圖示檔時沿用預設。"""
    if sys.platform == "win32":
        try:
            import ctypes
            # 讓工作列顯示本程式的圖示，而不是 python.exe 的
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoDoctorChecker")
        except Exception:
            pass
        if ICON_ICO.exists():
            try:
                window.iconbitmap(default=str(ICON_ICO))  # default=：之後的 Toplevel 也套用
                return
            except tk.TclError:
                pass
    if ICON_PNG.exists():
        try:
            window.iconphoto(True, tk.PhotoImage(file=str(ICON_PNG)))
        except tk.TclError:
            pass


def pick_font_family():
    available = set(tkfont.families(root))
    for family in ("Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC"):
        if family in available:
            return family
    return tkfont.nametofont("TkDefaultFont").actual("family")


def setup_style():
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=F_BASE)
    style.configure("TLabel", background=BG)
    style.configure("Section.TLabel", foreground=MUTED, font=F_SMALL)
    style.configure("Card.TLabel", background=CARD)
    style.configure("Field.TLabel", background=CARD, foreground=MUTED, font=F_SMALL)
    style.configure("Hint.TLabel", background=CARD, foreground=HINT, font=F_TINY)
    style.configure("Error.TLabel", background=CARD, foreground=RED, font=F_TINY)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=F_SMALL)
    style.configure("CardValue.TLabel", background=CARD, foreground=TEXT, font=F_VALUE)
    style.configure("CardTiny.TLabel", background=CARD, foreground=GRAY, font=F_TINY)

    style.configure("TEntry", fieldbackground="white", bordercolor=INPUT_BORDER,
                    lightcolor=INPUT_BORDER, darkcolor=INPUT_BORDER, padding=(6, 4))
    style.map("TEntry",
              fieldbackground=[("disabled", "#F0F3F5")],
              foreground=[("disabled", GRAY)],
              bordercolor=[("disabled", INPUT_BORDER), ("focus", TEAL)],
              lightcolor=[("disabled", INPUT_BORDER), ("focus", TEAL)])

    style.configure("TButton", background="white", foreground=TEXT, bordercolor=INPUT_BORDER,
                    lightcolor="white", darkcolor="white", padding=(14, 5), focuscolor=TEAL)
    style.map("TButton", background=[("disabled", "#F0F3F5"), ("active", "#EEF2F4")],
              foreground=[("disabled", HINT)])
    style.configure("Primary.TButton", background=TEAL, foreground="white", bordercolor=TEAL,
                    lightcolor=TEAL, darkcolor=TEAL, font=F_BUTTON, padding=(18, 5),
                    focuscolor="#7FD3CF")
    style.map("Primary.TButton",
              background=[("disabled", "#9FCFCF"), ("active", TEAL_DARK)],
              bordercolor=[("disabled", "#9FCFCF")],
              lightcolor=[("disabled", "#9FCFCF"), ("active", TEAL_DARK)],
              darkcolor=[("disabled", "#9FCFCF"), ("active", TEAL_DARK)],
              foreground=[("disabled", "white")])
    style.configure("Danger.TButton", background=RED_BG, foreground="white", bordercolor=RED_BG,
                    lightcolor=RED_BG, darkcolor=RED_BG, font=F_ALARM_BTN, padding=(10, 9),
                    focuscolor=RED_BG)
    style.map("Danger.TButton", background=[("active", RED)],
              lightcolor=[("active", RED)], darkcolor=[("active", RED)])

    style.configure("Teal.Horizontal.TProgressbar", troughcolor="#E3EEEE", background=TEAL_LIGHT,
                    bordercolor="#E3EEEE", lightcolor=TEAL_LIGHT, darkcolor=TEAL_LIGHT, thickness=10)

    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    bordercolor=BORDER, rowheight=30, font=F_BASE)
    style.map("Treeview", background=[("selected", TEAL_BG)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background="#EEF2F4", foreground=MUTED, font=F_SMALL_BOLD,
                    bordercolor=BORDER, lightcolor="#EEF2F4", darkcolor="#EEF2F4", padding=(6, 5))
    style.map("Treeview.Heading", background=[("active", "#E3E8EC")])


def card(parent, **pack):
    frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightcolor=BORDER,
                     highlightthickness=1)
    frame.pack(fill="x", **pack)
    return frame


root = tk.Tk()
root.title("自動雲端候診提醒")
root.configure(bg=BG)
root.resizable(False, False)
set_app_icon(root)

FONT = pick_font_family()
F_BASE = (FONT, 10)
F_BASE_L = (FONT, 12)
F_SMALL = (FONT, 9)
F_SMALL_BOLD = (FONT, 9, "bold")
F_TINY = (FONT, 8)
F_VALUE = (FONT, 11, "bold")
F_BUTTON = (FONT, 10, "bold")
F_HEADER = (FONT, 15, "bold")
F_BIG_NUM = (FONT, 28, "bold")
F_BIG_TEXT = (FONT, 12, "bold")
F_TITLE = (FONT, 17, "bold")
F_ALARM_TITLE = (FONT, 20, "bold")
F_ALARM_NUM = (FONT, 18, "bold")
F_ALARM_BTN = (FONT, 12, "bold")
setup_style()

try:
    _icon_full = tk.PhotoImage(file=str(ICON_PNG))
    ICON_SMALL = _icon_full.subsample(6)   # 約 43px：標題列
    ICON_ALARM = _icon_full.subsample(4)   # 64px：鬧鐘視窗
except tk.TclError:
    ICON_SMALL = ICON_ALARM = None

# 標題列
header = tk.Frame(root, bg=TEAL_DARK, padx=18, pady=12)
header.pack(fill="x")
if ICON_SMALL is not None:
    tk.Label(header, image=ICON_SMALL, bg=TEAL_DARK).pack(side="left", padx=(0, 12))
header_text = tk.Frame(header, bg=TEAL_DARK)
header_text.pack(side="left")
tk.Label(header_text, text="自動雲端候診提醒", font=F_HEADER, fg="white", bg=TEAL_DARK).pack(anchor="w")
tk.Label(header_text, text="快輪到您時響鈴通知，不必一直盯著候診頁面",
         font=F_SMALL, fg="#D8F3F1", bg=TEAL_DARK).pack(anchor="w")

body = tk.Frame(root, bg=BG, padx=18, pady=14)
body.pack(fill="both", expand=True)

# 輸入卡
input_card = card(body, pady=(0, 12))
form = tk.Frame(input_card, bg=CARD, padx=14, pady=12)
form.pack(fill="x")
for col in range(3):
    form.columnconfigure(col, weight=1, uniform="field")


def add_field(row, col, label, hint, colspan=1):
    ttk.Label(form, text=label, style="Field.TLabel").grid(
        row=row, column=col, columnspan=colspan, sticky="w", padx=(0 if col == 0 else 6, 0))
    entry = ttk.Entry(form, font=F_BASE)
    entry.grid(row=row + 1, column=col, columnspan=colspan, sticky="ew",
               padx=(0 if col == 0 else 6, 0), pady=(3, 0))
    hint_label = ttk.Label(form, text=hint, style="Hint.TLabel")
    hint_label.grid(row=row + 2, column=col, columnspan=colspan, sticky="w",
                    padx=(0 if col == 0 else 6, 0), pady=(2, 6))
    FIELD_HINTS[entry] = (hint_label, hint)
    return entry


url_entry = add_field(0, 0, "候診網址", "在全國雲端候診中心找到診所後，複製該頁網址", colspan=3)
clinic_entry = add_field(3, 0, "診間", "例：1、01、第一")
sequence_entry = add_field(3, 1, "您的門診序號", "掛號單上的號碼")
alarm_time = add_field(3, 2, "提前幾分鐘提醒", "預設每號約 5 分鐘")

buttons = tk.Frame(form, bg=CARD)
buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(4, 0))
fetch_button = ttk.Button(buttons, text="▶ 開始監控", style="Primary.TButton", command=start_monitoring)
fetch_button.pack(side="left", padx=(0, 8))
stop_button = ttk.Button(buttons, text="■ 停止", command=lambda: stop_monitoring("已停止監控"))
stop_button.pack(side="left")
stop_button.state(["disabled"])

# 按 Enter 移到下一欄；在最後一欄按 Enter 開始監控
url_entry.bind("<Return>", lambda e: clinic_entry.focus())
clinic_entry.bind("<Return>", lambda e: sequence_entry.focus())
sequence_entry.bind("<Return>", lambda e: alarm_time.focus())
alarm_time.bind("<Return>", lambda e: start_monitoring())

# 狀態卡
status_card = card(body, pady=(0, 12))
status_accent = tk.Frame(status_card, width=5, bg=GRAY)
status_accent.pack(side="left", fill="y")
status_body = tk.Frame(status_card, bg=CARD, padx=14, pady=10)
status_body.pack(side="left", fill="both", expand=True)

big_row = tk.Frame(status_body, bg=CARD)
big_row.pack(fill="x")
big_number = ttk.Label(big_row, style="Card.TLabel", font=F_BIG_NUM)
big_text = ttk.Label(big_row, style="Card.TLabel", font=F_TITLE)
big_text.pack(side="left", anchor="s", pady=(0, 6), padx=(2, 0))

facts_frame = tk.Frame(status_body, bg=CARD)
facts_frame.pack(fill="x", pady=(4, 0))

progress_frame = tk.Frame(status_body, bg=CARD)
progress_bar = ttk.Progressbar(progress_frame, style="Teal.Horizontal.TProgressbar", maximum=100)
progress_bar.pack(fill="x")
bar_labels = tk.Frame(progress_frame, bg=CARD)
bar_labels.pack(fill="x", pady=(2, 0))
bar_left_label = ttk.Label(bar_labels, style="CardTiny.TLabel")
bar_left_label.pack(side="left")
bar_right_label = ttk.Label(bar_labels, style="CardTiny.TLabel")
bar_right_label.pack(side="right")

foot_frame = tk.Frame(status_body, bg=CARD)
foot_frame.pack(fill="x", pady=(8, 0))
foot_dot = ttk.Label(foot_frame, text="●", style="CardTiny.TLabel")
foot_dot.pack(side="left")
foot_left_label = ttk.Label(foot_frame, style="CardTiny.TLabel")
foot_left_label.pack(side="left", padx=(3, 0))
foot_right_label = ttk.Label(foot_frame, style="CardTiny.TLabel")
foot_right_label.pack(side="right")

# 候診表格
ttk.Label(body, text="本院候診狀況", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
columns = (("clinic", "診間", 150, "w"), ("doctor", "醫師", 120, "w"), ("slot", "時段", 60, "w"),
           ("seq", "目前號碼", 80, "e"), ("wait", "等待人數", 80, "e"))
result_table = ttk.Treeview(body, columns=[c[0] for c in columns], show="headings", height=2,
                            selectmode="none")
for key, title, width, anchor in columns:
    result_table.heading(key, text=title, anchor=anchor)
    result_table.column(key, width=width, anchor=anchor, stretch=key == "doctor")
result_table.tag_configure("mine", background=TEAL_BG, font=(FONT, 10, "bold"))
result_table.pack(fill="x")

show_status("idle", "尚未開始監控", message="輸入資料後按「開始監控」，快輪到您時會響鈴並跳出提醒。",
            foot_left="未開始")
root.protocol("WM_DELETE_WINDOW", on_close)
root.after(1000, tick)

# 帶入上次輸入的網址、診間、提前分鐘數；游標停在門診序號
_saved = settings.load()
for _entry, _key in ((url_entry, "url"), (clinic_entry, "clinic"), (alarm_time, "minutes")):
    _entry.insert(0, _saved.get(_key, ""))
(sequence_entry if _saved else url_entry).focus_set()

if __name__ == "__main__":
    root.mainloop()
