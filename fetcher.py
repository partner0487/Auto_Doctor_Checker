"""用 Selenium 抓取雲端候診表格。

同一個 Chrome 會重複使用（第一次之後只重新載入頁面），
所有方法都應在同一個背景執行緒呼叫（見 DoctorChecker.py 的 ThreadPoolExecutor）。
"""
from selenium.common.exceptions import TimeoutException, WebDriverException
# 直接 import Chrome 模組（不用 webdriver.Chrome 的延遲載入），PyInstaller 才分析得到
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.webdriver import WebDriver as Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# 表格 id 形如 dnn_ctr655_..._visWInfos_gvwCRoom，中間的模組編號可能因診所而異，只比對結尾
TABLE_SELECTOR = "table[id$='visWInfos_gvwCRoom']"
LOADING_TEXT = "載入中"


class FetchError(Exception):
    pass


class WaitlistFetcher:
    def __init__(self, timeout: float = 20):
        self.timeout = timeout
        self._driver = None

    def _ensure_driver(self):
        if self._driver is None:
            options = ChromeOptions()
            options.add_argument("--headless=new")
            options.page_load_strategy = "eager"  # 不等圖片／外部資源，HTML 就緒即可
            self._driver = Chrome(options=options)  # Selenium 4.6+ 會自動下載 ChromeDriver
            self._driver.set_page_load_timeout(self.timeout)
        return self._driver

    def _read_rows(self, driver):
        """讀取表格；資料還在「載入中」時回傳 None，讓 WebDriverWait 繼續等。"""
        tables = driver.find_elements(By.CSS_SELECTOR, TABLE_SELECTOR)
        if not tables:
            return None
        data = []
        for row in tables[0].find_elements(By.CSS_SELECTOR, "tr.table-data-row"):
            cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
            if len(cells) != 5:
                continue
            if any(LOADING_TEXT in c for c in cells):
                return None
            data.append(tuple(cells))
        return data or None

    def fetch(self, url: str):
        """回傳 [(診間名稱, 看診醫師, 時段, 門診序, 等待), ...]；失敗時拋出 FetchError。"""
        try:
            driver = self._ensure_driver()
            try:
                driver.get(url)
            except TimeoutException:
                # 有些資源載不完，但表格可能已經有資料：停止載入後照常讀取
                driver.execute_script("window.stop();")
            return WebDriverWait(driver, self.timeout).until(self._read_rows)
        except TimeoutException as e:
            raise FetchError("網頁載入逾時，或找不到候診表格（請確認網址）") from e
        except WebDriverException as e:
            self.close()  # 瀏覽器可能已損壞，下次重新開
            raise FetchError(f"瀏覽器錯誤：{e.msg or e}") from e

    def close(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
