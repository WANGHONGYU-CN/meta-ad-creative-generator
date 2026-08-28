"""全量重建 SQLite 索引库：扫描 outputs/*/manifest.json 导入 data/app.db。

用途：
- 首次接入数据库时导入历史 run（即"本地数据迁移"）；
- 库文件损坏/误删/怀疑与 manifest 不一致时，随时重跑修复。

运行：~/venvs/meta-creative-tool/bin/python scripts/rebuild_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db  # noqa: E402


def main() -> None:
    result = db.rebuild_from_outputs()
    print(f"导入完成：{result['imported']} 个 run 已入库 → {db.DB_PATH}")
    for dir_name, msg in result["errors"]:
        print(f"  [跳过] {dir_name}: {msg}")
    if result["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
