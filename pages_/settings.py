"""设置页：填写 API key、下拉选择模型、测试连接。"""
import os

import streamlit as st

from core import imagen, llm
from core.config import ENV_FALLBACK, load_config, save_config

st.title("🔑 设置")
st.caption(
    "Key 优先从环境变量读取（ANTHROPIC_API_KEY / OPENAI_API_KEY / *_BASE_URL），"
    "此处填写会覆盖环境变量；只保存在本机 config.json。"
)

# 设置页编辑的是 config.json 原始值；环境变量只提示状态，不回填（避免保存时把 key 落盘）
config = load_config(env_fallback=False)
effective = load_config()
ss = st.session_state

env_status = "、".join(
    f"`{name}` {'✅' if os.environ.get(name) else '❌'}" for name in ENV_FALLBACK.values()
)
st.info(f"当前环境变量状态：{env_status}（❌ 表示未检测到，需在启动工具的终端里生效）")

MANUAL = "✍️ 手动输入…"


def fetch_models():
    """拉取两侧模型列表，存入 session_state。"""
    cfg = load_config()
    try:
        ss.llm_models = llm.list_models(cfg)
    except Exception as e:  # noqa: BLE001
        ss.llm_models = []
        st.warning(f"Claude 模型列表拉取失败（可先手动输入）：{e}")
    try:
        ss.img_models = imagen.list_models(cfg)
    except Exception as e:  # noqa: BLE001
        ss.img_models = []
        st.warning(f"生图模型列表拉取失败（可先手动输入）：{e}")


# 首次进入且 key 已配好（含环境变量）时自动拉一次
if "llm_models" not in ss and (effective["anthropic_api_key"] or effective["openai_api_key"]):
    with st.spinner("正在拉取可用模型列表…"):
        fetch_models()

if st.button("🔄 刷新模型列表"):
    with st.spinner("正在拉取可用模型列表…"):
        fetch_models()


def model_selector(label: str, models: list, current: str, key: str) -> str:
    """下拉选择模型；列表为空或选「手动输入」时退回文本框。当前值不在列表中也会保留。"""
    options = list(models)
    if current and current not in options:
        options.insert(0, current)
    options.append(MANUAL)
    if not models and not current:
        return st.text_input(label, value=current, key=f"{key}_text_only")
    choice = st.selectbox(
        label,
        options,
        index=options.index(current) if current in options else 0,
        key=f"{key}_select",
    )
    if choice == MANUAL:
        return st.text_input(f"{label}（手动输入）", value=current, key=f"{key}_text")
    return choice


st.subheader("Claude（找场景 / 生成生图提示词 / 看图写文案）")
anthropic_key = st.text_input(
    "Anthropic API Key（环境变量已配置时留空即可）",
    value=config["anthropic_api_key"], type="password",
)
claude_model = model_selector(
    "Claude 模型", ss.get("llm_models", []), config["claude_model"], "claude_model"
)
anthropic_base_url = st.text_input(
    "Anthropic Base URL（可选，走中转/代理时填写）", value=config["anthropic_base_url"]
)

st.divider()

st.subheader("生图（OpenAI 兼容接口）")
openai_key = st.text_input(
    "生图 API Key（环境变量已配置时留空即可）",
    value=config["openai_api_key"], type="password",
)
image_model = model_selector(
    "生图模型", ss.get("img_models", []), config["image_model"], "image_model"
)
openai_base_url = st.text_input(
    "生图 Base URL（可选，走中转/代理时填写）", value=config["openai_base_url"]
)

st.divider()

col_save, col_test = st.columns([1, 2])
with col_save:
    if st.button("💾 保存设置", type="primary", use_container_width=True):
        save_config(
            {
                "anthropic_api_key": anthropic_key.strip(),
                "anthropic_base_url": anthropic_base_url.strip(),
                "claude_model": claude_model.strip(),
                "openai_api_key": openai_key.strip(),
                "openai_base_url": openai_base_url.strip(),
                "image_model": image_model.strip(),
            }
        )
        st.success("已保存到 config.json")

with col_test:
    if st.button("🔌 测试连接（先保存再测试）", use_container_width=True):
        cfg = load_config()
        with st.spinner("测试 Claude 连接中…"):
            try:
                reply = llm.test_connection(cfg)
                st.success(f"Claude 连接成功：{reply}")
            except Exception as e:  # noqa: BLE001
                st.error(f"Claude 连接失败：{e}")
        with st.spinner("测试生图接口连接中…"):
            try:
                imagen.test_connection(cfg)
                st.success("生图接口连接成功")
            except Exception as e:  # noqa: BLE001
                st.error(f"生图接口连接失败：{e}")
