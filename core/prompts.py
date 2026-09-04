"""6 套提示词模板的读写与变量渲染（存储在 PostgreSQL，决策 20）。

- 当前生效版在 prompt_templates 表（管理页编辑对象），出厂基线在 prompt_defaults 表；
  「恢复默认」= reset_prompt() 把 prompt_defaults 对应行整行拷回；
- 出厂文本内嵌在 alembic 0002 seed migration 里（新环境 upgrade head 即得），
  原 DEFAULT_PROMPTS 常量与 prompts.json 已随第二阶段退役；
- 模板中的变量写作 {variable_name}，渲染时逐个字符串替换（不用 str.format，
  这样模板里可以放 JSON 示例的花括号而不需要转义）。
"""


def _session():
    # 惰性 import：import 本模块不要求数据库环境就绪
    from server.db.session import get_session_factory

    return get_session_factory()()


def _as_dict(row) -> dict:
    return {
        "name": row.name,
        "description": row.description,
        "variables": list(row.variables or []),
        "template": row.template,
    }


def load_prompts() -> dict:
    """全部提示词（当前生效版），结构与旧版一致：{key: {name, description, variables, template}}。"""
    from sqlalchemy import select

    from server.db.models import PromptTemplate

    with _session() as session:
        rows = session.execute(
            select(PromptTemplate).order_by(PromptTemplate.id)
        ).scalars().all()
        return {row.key: _as_dict(row) for row in rows}


def save_prompts(prompts: dict) -> None:
    """按 key 覆盖 template（与旧版 prompts.json 语义一致：可变量只有 template）。"""
    from sqlalchemy import select

    from server.db.models import PromptTemplate

    with _session() as session:
        for row in session.execute(select(PromptTemplate)).scalars():
            item = prompts.get(row.key)
            if isinstance(item, dict) and item.get("template"):
                row.template = item["template"]
        session.commit()


def reset_prompt(key: str) -> dict | None:
    """恢复出厂默认：prompt_defaults 对应行整行拷回 prompt_templates。key 无出厂值返回 None。"""
    from sqlalchemy import select

    from server.db.models import PromptDefault, PromptTemplate

    with _session() as session:
        default = session.execute(
            select(PromptDefault).where(PromptDefault.key == key)
        ).scalar_one_or_none()
        if default is None:
            return None
        row = session.execute(
            select(PromptTemplate).where(PromptTemplate.key == key)
        ).scalar_one_or_none()
        if row is None:
            row = PromptTemplate(key=key)
            session.add(row)
        row.name = default.name
        row.description = default.description
        row.variables = list(default.variables or [])
        row.template = default.template
        session.commit()
        return _as_dict(row)


def render(template: str, variables: dict) -> str:
    """把模板中的 {key} 逐个替换为对应值。"""
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", str(value))
    return result
