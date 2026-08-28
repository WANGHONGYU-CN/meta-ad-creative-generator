"""运行结果落盘：run 文件夹、图片、manifest.json、交付表.xlsx。"""
import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from core import db
from core.config import OUTPUTS_DIR


def _sanitize(name: str, max_len: int = 20) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return cleaned[:max_len] or "未命名"


def create_run_dir(product_hint: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = OUTPUTS_DIR / f"{stamp}_{_sanitize(product_hint)}"
    (run_dir / "images").mkdir(parents=True, exist_ok=True)
    return run_dir


def image_filename(main_scene: str, sub_scene: str, ratio: str) -> str:
    return f"{_sanitize(main_scene)}_{_sanitize(sub_scene)}_{ratio.replace(':', 'x')}.png"


def save_image(run_dir: Path, filename: str, png_bytes: bytes) -> Path:
    path = run_dir / "images" / filename
    path.write_bytes(png_bytes)
    return path


def save_manifest(run_dir: Path, manifest: dict) -> str:
    """落盘 manifest.json（权威数据），并同步写 SQLite 索引（失败不中断，返回错误信息）。"""
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return db.sync_run_safe(run_dir, manifest)


def export_xlsx(run_dir: Path, jobs: list) -> Path:
    """每套文案一行，与图片文件名绑定，投放团队可直接使用。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "投放素材"
    headers = ["图片文件名", "主场景", "细分场景", "尺寸", "文案序号", "角度", "标题 Headline", "主文案 Primary Text", "生图提示词"]
    ws.append(headers)
    for job in jobs:
        copies = job.get("copies") or [{}]
        for i, copy in enumerate(copies, start=1):
            ws.append([
                job.get("filename", ""),
                job.get("main_scene", ""),
                job.get("sub_scene", ""),
                job.get("ratio", ""),
                i,
                copy.get("angle", ""),
                copy.get("headline", ""),
                copy.get("primary_text", ""),
                job.get("image_prompt", ""),
            ])
    # 简单列宽，便于直接打开阅读
    widths = [40, 14, 18, 8, 8, 14, 40, 60, 60]
    for col, width in zip("ABCDEFGHI", widths):
        ws.column_dimensions[col].width = width
    path = run_dir / "交付表.xlsx"
    wb.save(path)
    return path
