"""Meta 投放素材生产工具 —— 入口。

运行：streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Meta 素材工厂", page_icon="🎨", layout="wide")

# 切页保活：带 key 的输入组件在「未被渲染的一次重跑」后会被 Streamlit 自动回收，
# 导致切到别的页面再回来时 Step 0 填写的内容被清空。app.py 在每次重跑、进入任何
# 页面之前都会执行，此处重新赋值可把这些 key 标记为用户状态，跳过组件回收。
for _k in ("product_info", "ratio_choice", "title_count"):
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]

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
