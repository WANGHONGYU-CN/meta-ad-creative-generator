"""一次性脚本：把本机 prompts.json 里改过的提示词导入 prompt_templates。

用法（项目根目录，需已 `alembic upgrade head` 且配好 DATABASE_URL）：
    ~/venvs/meta-creative-tool/bin/python scripts/import_prompts_json.py [prompts.json 路径]

语义与旧 core.prompts.load_prompts 一致：prompts.json 只覆盖 template 字段
（name/description/variables 保持出厂值）；库里没有的 key 跳过并提示。
跑完本脚本后，运行时不再依赖 prompts.json。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from server.db.models import PromptTemplate  # noqa: E402
from server.db.session import get_session_factory  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("prompts.json")
    if not path.exists():
        print(f"找不到 {path}，未做任何修改")
        return 1
    saved = json.loads(path.read_text(encoding="utf-8"))

    updated, skipped = [], []
    with get_session_factory()() as session:
        for key, item in saved.items():
            if not (isinstance(item, dict) and item.get("template")):
                skipped.append(f"{key}（无 template 字段）")
                continue
            row = session.execute(
                select(PromptTemplate).where(PromptTemplate.key == key)
            ).scalar_one_or_none()
            if row is None:
                skipped.append(f"{key}（库中不存在此 key）")
                continue
            row.template = item["template"]
            updated.append(key)
        session.commit()

    print(f"已覆盖 prompt_templates.template：{updated or '（无）'}")
    if skipped:
        print(f"跳过：{skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
