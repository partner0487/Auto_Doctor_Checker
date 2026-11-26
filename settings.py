"""記住上次輸入的網址、診間、提前提醒分鐘數（門診序號每次不同，不儲存）。"""
import json
import os
from pathlib import Path

SAVED_FIELDS = ("url", "clinic", "minutes")


def default_path() -> Path:
    base = os.environ.get("APPDATA") or Path.home() / ".config"
    return Path(base) / "AutoDoctorChecker" / "settings.json"


def load(path: Path = None) -> dict:
    """讀取設定；檔案不存在或損壞時回傳空 dict。"""
    path = path or default_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data[k]) for k in SAVED_FIELDS if k in data}


def save(values: dict, path: Path = None) -> bool:
    """儲存設定；失敗（例如沒有寫入權限）時回傳 False，不影響主程式。"""
    path = path or default_path()
    data = {k: str(values[k]) for k in SAVED_FIELDS if k in values}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False
