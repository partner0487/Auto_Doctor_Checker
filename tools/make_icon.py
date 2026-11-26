"""把 assets/icon.svg 轉成 assets/icon.png（256px）與 assets/icon.ico（16～256px）。

用法（需要 Chrome 與 Pillow）：
    pip install pillow
    python tools/make_icon.py
"""
import io
from pathlib import Path

from PIL import Image
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By

ASSETS = Path(__file__).resolve().parent.parent / "assets"
RENDER_SIZE = 1024  # 先渲染大圖再高品質縮小
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render_svg(svg_path: Path, size: int) -> Image.Image:
    svg = svg_path.read_text(encoding="utf-8").replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
    html = svg_path.with_name("_icon_render.html")
    html.write_text(
        "<!doctype html><html><body style='margin:0;background:transparent'>"
        f"<div id='icon' style='width:{size}px;height:{size}px'>{svg}</div></body></html>",
        encoding="utf-8",
    )
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--force-device-scale-factor=1")
    options.add_argument(f"--window-size={size + 100},{size + 300}")
    driver = WebDriver(options=options)
    try:
        # 透明背景
        driver.execute_cdp_cmd("Emulation.setDefaultBackgroundColorOverride",
                               {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        driver.get(html.as_uri())
        png = driver.find_element(By.ID, "icon").screenshot_as_png
    finally:
        driver.quit()
        html.unlink()
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main():
    big = render_svg(ASSETS / "icon.svg", RENDER_SIZE)
    frames = [big.resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    frames[-1].save(ASSETS / "icon.png")
    frames[-1].save(ASSETS / "icon.ico", format="ICO",
                    sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1])
    print(f"已產生 {ASSETS / 'icon.png'} 與 {ASSETS / 'icon.ico'}（{ICO_SIZES}）")


if __name__ == "__main__":
    main()
