"""提示词管理页：7 套提示词在线编辑、保存、恢复默认。"""
import streamlit as st

from core.prompts import DEFAULT_PROMPTS, load_prompts, save_prompts

st.title("📝 提示词管理")
st.caption("模板中的 {变量名} 会在运行时自动替换，请保留需要的变量占位符。修改后点击对应的保存按钮生效。")

prompts = load_prompts()

for key, item in prompts.items():
    with st.expander(f"{item['name']} —— {item['description']}", expanded=False):
        st.markdown(
            "可用变量：" + "、".join(f"`{{{v}}}`" for v in item["variables"])
        )
        edited = st.text_area(
            "模板内容",
            value=item["template"],
            height=380,
            key=f"tpl_{key}",
            label_visibility="collapsed",
        )
        col_save, col_reset = st.columns([1, 1])
        with col_save:
            if st.button("💾 保存", key=f"save_{key}"):
                prompts[key]["template"] = edited
                save_prompts(prompts)
                st.success("已保存")
        with col_reset:
            if st.button("↩️ 恢复默认", key=f"reset_{key}"):
                prompts[key]["template"] = DEFAULT_PROMPTS[key]["template"]
                save_prompts(prompts)
                st.rerun()
