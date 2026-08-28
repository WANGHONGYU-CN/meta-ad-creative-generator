"""Meta 投放素材生产工具 —— 入口。

运行：streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Meta 素材工厂", page_icon="🎨", layout="wide")

pg = st.navigation(
    [
        st.Page("pages_/workflow.py", title="素材工作流", icon="🎨", default=True),
        st.Page("pages_/scene_library.py", title="场景库", icon="🗃"),
        st.Page("pages_/history.py", title="历史素材", icon="🗂️"),
        st.Page("pages_/prompts_editor.py", title="提示词管理", icon="📝"),
        st.Page("pages_/settings.py", title="设置（API Key）", icon="🔑"),
    ]
)
pg.run()
