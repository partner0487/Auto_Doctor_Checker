import tkinter as tk
import threading
from tkinter import Tk, Label, Entry, Button, messagebox, ttk
from PIL import Image, ImageTk
from tkinter import messagebox
from selenium import webdriver
from selenium.webdriver.common.by import By
import time
import pygame
import re
from word2number import w2n
import roman
import math
import unicodedata

pygame.mixer.init()

# 中文數字轉換字典
chinese_num_map = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000, "萬": 10000
}

global_data = []

def is_roman(s):
    try:
        roman.fromRoman(s.upper())  # 嘗試轉換
        return True
    except roman.InvalidRomanNumeralError:
        return False
    
def chinese_to_int(text):
    if not text:
        return 0

    # 處理「零」
    if text == "零":
        return 0

    # 處理「十」開頭的數字（如「十一」）
    if text.startswith("十"):
        text = "一" + text  # 將「十一」轉為「一十一」

    # 拆分數字（如「二十一」 -> ["二", "十", "一"]）
    parts = re.findall(r"[零一二三四五六七八九十百千萬]", text)
    if not parts:
        return 0

    # 計算數值
    result = 0
    temp = 0
    for part in parts:
        if part in ["十", "百", "千", "萬"]:
            if temp == 0:
                temp = 1  # 處理「十」單獨出現的情況
            result += temp * chinese_num_map[part]
            temp = 0
        else:
            temp += chinese_num_map[part]
    result += temp  # 加上最後的個位數

    return result

# 將各種形式的數字轉換為整數
def convert_to_int(text):
    # 嘗試提取純數字（如 "01" -> 1）
    num_match = re.search(r"\d+", text)
    if num_match:
        return int(num_match.group())

    text = text.strip().upper()  # 確保大小寫一致
    if is_roman(text):  
        return roman.fromRoman(text)  # 轉換羅馬數字
    try:
        return w2n.word_to_num(text)  # 轉換英文數字
    except ValueError:
        chinese_num_match = re.search(r"[零一二三四五六七八九十百千萬]+", text)
        if chinese_num_match:
            chinese_num = chinese_num_match.group()
            return chinese_to_int(chinese_num)


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

def to_halfwidth(data):
    return ''.join(
        chr(ord(c) - 0xFEE0) if unicodedata.category(c) == 'Nd' and '０' <= c <= '９' else c
        for c in data
    )

# 顯示表格數據的函數
def show_table_data():
    url = url_entry.get()
    if not url:
        messagebox.showwarning("輸入錯誤", "請輸入網址")
        return

    # 獲取表格數據
    data = fetch_table_data(url)
    if not data:
        messagebox.showwarning("警告", "無法獲取有效數據，請稍後重試")
        return
    
    global global_data
    tmp = [
        (
            convert_to_int(to_halfwidth(record[0])),  # 將第 0 個元素進行轉換
            record[1],  # 保留第 1 個元素不變
            record[2],  # 保留第 2 個元素不變
            convert_to_int(to_halfwidth(record[3])),  # 將第 3 個元素進行轉換
            convert_to_int(to_halfwidth(record[4]))   # 將第 4 個元素進行轉換
        )
        for record in data
    ]
    if global_data != tmp:
        # 清空之前的顯示
        for row in result_table.get_children():
            result_table.delete(row)

        # 將數據插入到表格中
        for row in data:
            result_table.insert('', 'end', values=row)

        global_data = tmp

    clinic = convert_to_int(clinic_entry.get())
    if clinic > len(global_data):
        messagebox.showwarning("錯誤", "您的診間超出該院診間數")
        return
    
    sequence = convert_to_int(sequence_entry.get())
    if sequence < global_data[clinic-1][3]:
        messagebox.showwarning("錯誤", "您的門診序已過號")
        return
    
    root.after(30000 , show_table_data)  # 每 30 秒後再次執行

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

def check_alarm():
    alarm = math.ceil(convert_to_int(alarm_time.get()) / 5)
    clinic = convert_to_int(clinic_entry.get()) - 1 
    sequence = convert_to_int(sequence_entry.get())
    while not stop_event.is_set():  # 當 stop_event 被設置為 True 時，退出執行緒
        global global_data
        if sequence - global_data[clinic][3] <= alarm:
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
    show_table_data()  # 立即執行一次
    threading.Thread(target=check_alarm).start()  # 用執行緒執行，避免卡住 GUI

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
result_table = tk.ttk.Treeview(root, columns=columns, show='headings')
for col in columns:
    result_table.heading(col, text=col)
    result_table.column(col, width=100)
result_table.grid(row=5, column=0, columnspan=2, padx=10, pady=10)

# 啟動主循環
root.protocol("WM_DELETE_WINDOW", stop_check_alarm)

root.mainloop()