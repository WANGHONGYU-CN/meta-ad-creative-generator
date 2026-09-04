"""文件落盘工具：图片命名/写盘、交付表.xlsx。

run 目录创建与 manifest 落盘已随数据层切 PostgreSQL 移除（决策 20）：
目录由 server/services/state_store.create_run 按 run_{id} 创建，
manifest.json 只在导出交付包时由库内数据现场生成（协议不变）。
"""
import re
from pathlib import Path

from openpyxl import Workbook


def _sanitize(name: str, max_len: int = 20) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return cleaned[:max_len] or "未命名"


def image_filename(main_scene: str, sub_scene: str, ratio: str) -> str:
    return f"{_sanitize(main_scene)}_{_sanitize(sub_scene)}_{ratio.replace(':', 'x')}.png"


def save_image(run_dir: Path, filename: str, png_bytes: bytes) -> Path:
    path = Path(run_dir) / "images" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)
    return path


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
    path = Path(run_dir) / "交付表.xlsx"
    wb.save(path)
    return path
