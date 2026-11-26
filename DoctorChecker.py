import tkinter as tk
import threading
from tkinter import messagebox, ttk
from selenium import webdriver
from selenium.webdriver.common.by import By
import time
import pygame

from waitlist import convert_to_int, find_clinic, parse_row, should_alarm

pygame.mixer.init()

global_data = []  # list[ClinicRow]


def read_inputs():
    """讀取並驗證使用者輸入，回傳 (clinic, sequence, minutes)；不合法時跳警告並回傳 None。"""
    clinic = convert_to_int(clinic_entry.get())
    sequence = convert_to_int(sequence_entry.get())
    minutes = convert_to_int(alarm_time.get())
    if clinic is None or clinic <= 0:
        messagebox.showwarning("輸入錯誤", "請輸入正確的診間（例如 1、01、第一）")
        return None
    if sequence is None or sequence <= 0:
        messagebox.showwarning("輸入錯誤", "請輸入正確的門診序號（例如 15）")
        return None
    if minutes is None or minutes < 0:
        messagebox.showwarning("輸入錯誤", "請輸入提前幾分鐘提醒（例如 30）")
        return None
    return clinic, sequence, minutes


# 爬蟲函數：從網頁中提取表格數據
def fetch_table_data(url):
    try:
        # 啟動瀏覽器
        option=webdriver.ChromeOptions()
        option.add_argument("--headless")
        driver = webdriver.Chrome(options=option)  # 確保 ChromeDriver 已安裝
        driver.get(url)

        # 等待數據加載
        # time.sleep(10)  # 根據網頁加載速度調整等待時間

        # 找到指定的表格
        table = driver.find_element(By.ID, 'dnn_ctr655_ViewVWWL_Clinics_mcsModuleCont_ctl00_visWInfos_gvwCRoom')
        rows = table.find_elements(By.CLASS_NAME, 'table-data-row')

        data = []
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, 'td')
            if len(cells) == 5:  # 確保每行有 5 個欄位
                clinic_name = cells[0].text.strip()
                doctor = cells[1].text.strip()
                time_slot = cells[2].text.strip()
                sequence = cells[3].text.strip()
                waiting = cells[4].text.strip()
                data.append((clinic_name, doctor, time_slot, sequence, waiting))
        driver.quit()  # 關閉瀏覽器
        return data
    except Exception as e:
        messagebox.showerror("錯誤", f"爬取數據時發生錯誤: {e}")
        return []

# 顯示表格數據的函數
def show_table_data():
    url = url_entry.get()
    if not url:
        messagebox.showwarning("輸入錯誤", "請輸入網址")
        return

    inputs = read_inputs()
    if inputs is None:
        return
    clinic, sequence, _ = inputs

    # 獲取表格數據
    data = fetch_table_data(url)
    if not data:
        messagebox.showwarning("警告", "無法獲取有效數據，請稍後重試")
        return

    global global_data
    rows = [parse_row(record) for record in data]
    if global_data != rows:
        # 清空之前的顯示
        for item in result_table.get_children():
            result_table.delete(item)

        # 將數據插入到表格中
        for row in rows:
            result_table.insert('', 'end', values=row.raw)

        global_data = rows

    row = find_clinic(global_data, clinic)
    if row is None:
        available = "、".join(str(r.clinic) for r in global_data if r.clinic is not None)
        messagebox.showwarning("錯誤", f"找不到第 {clinic} 診（此院診間：{available or '無'}）")
        return

    # row.sequence 為 None 代表尚未開診，繼續等待
    if row.sequence is not None and sequence < row.sequence:
        messagebox.showwarning("錯誤", "您的門診序已過號")
        return

    root.after(30000, show_table_data)  # 每 30 秒後再次執行

def play_alarm_sound():
    pygame.mixer.music.load("alarm.mp3")  # 換成你的音效檔案
    pygame.mixer.music.play(loops=-1)  # 無限循環播放

def on_alarm_triggered():
    # 獲取主窗口的位置和大小
    main_x = root.winfo_x()
    main_y = root.winfo_y()
    main_width = root.winfo_width()
    main_height = root.winfo_height()
    # 彈出提示框，按下 OK 會觸發停止鬧鐘音效
    alarm_window = tk.Toplevel(root)
    alarm_window.title("🔔 鬧鐘提醒")
    alarm_window.geometry("250x100")
    # 計算 Toplevel 窗口的位置（居中於主窗口）
    window_width = 250
    window_height = 100
    x = main_x + (main_width - window_width) // 2
    y = main_y + (main_height - window_height) // 2
    alarm_window.geometry(f"+{x}+{y}")
    tk.Label(alarm_window, text="快到您看病了喔").pack(pady=10)
    close_button = tk.Button(alarm_window, text="關閉鬧鐘", command=lambda: close_alarm(alarm_window))
    close_button.pack(pady=10)
    alarm_window.protocol("WM_DELETE_WINDOW", lambda: close_alarm(alarm_window))  # 防止直接關閉視窗

def close_alarm(alarm_window):
    """ 停止鬧鐘音效並關閉自定義視窗 """
    pygame.mixer.music.stop()  # 停止播放音樂
    alarm_window.destroy()  # 關閉自定義視窗

stop_event = threading.Event()

def stop_check_alarm():
    stop_event.set()  # 設置 stop_event，終止執行緒
    root.destroy()  # 假设 root 是你的主窗口

def check_alarm(clinic, sequence, minutes):
    while not stop_event.is_set():  # 當 stop_event 被設置為 True 時，退出執行緒
        row = find_clinic(global_data, clinic)
        if row is not None and should_alarm(sequence, row.sequence, minutes):
            on_alarm_triggered()  # 彈出自定義視窗
            threading.Thread(target=play_alarm_sound).start()  # 用執行緒播放音樂
            break
        time.sleep(1)  # 每秒檢查一次條件

def on_enter(event, next_widget):
    next_widget.focus()  # 將焦點移到下一個 widget

def on_alarm_enter(event, fetch_button):
    # 當在 alarm_time 欄位按下 Enter 時，觸發 fetch_button 的點擊事件
    fetch_button.invoke()

def start_periodic_fetch():
    inputs = read_inputs()
    if inputs is None:
        return
    show_table_data()  # 立即執行一次
    threading.Thread(target=check_alarm, args=inputs).start()  # 用執行緒執行，避免卡住 GUI

# 創建主窗口
root = tk.Tk()
root.title("自動雲端候診提醒")

# 創建輸入欄位
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

url_entry.bind("<Return>", lambda event: on_enter(event, clinic_entry))  # 按下 Enter 從 url_entry 移到 clinic_entry
clinic_entry.bind("<Return>", lambda event: on_enter(event, sequence_entry))  # 按下 Enter 從 clinic_entry 移到 sequence_entry
sequence_entry.bind("<Return>", lambda event: on_enter(event, alarm_time))  # 按下 Enter 從 sequence_entry 移到 alarm_time
alarm_time.bind("<Return>", lambda event: on_alarm_enter(event, fetch_button))  # 按下 Enter 在 alarm_time 時觸發 fetch_button 的點擊事件

# 創建按鈕
fetch_button = tk.Button(root, text="獲取數據 (自動更新) ", command=start_periodic_fetch)
fetch_button.grid(row=4, column=1, pady=10)

# 創建顯示結果的表格
columns = ("診間名稱", "看診醫師", "時段", "門診序", "等待")
result_table = ttk.Treeview(root, columns=columns, show='headings')
for col in columns:
    result_table.heading(col, text=col)
    result_table.column(col, width=100)
result_table.grid(row=5, column=0, columnspan=2, padx=10, pady=10)

# 啟動主循環
root.protocol("WM_DELETE_WINDOW", stop_check_alarm)

root.mainloop()