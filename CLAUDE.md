# CLAUDE.md — Meta 素材工厂

> 本文件是项目的长期维护档案与协作规范。**每完成一个开发阶段必须更新本文件**，并保持与 README.md 同步。

## 项目目标

为 Meta（Facebook/Instagram）投放团队提供素材生产流水线工具：
**产品信息 → AI 挖掘投放场景（主场景+细分场景）→ 生成生图提示词 → 生图（1:1 / 4:5 / 双尺寸）→ AI 看图写多套标题文案并与图片绑定 → 导出交付包**。

定位：**生产项目，长期维护**，不是一次性脚本。使用者为投放团队成员，本地运行。

## 技术架构

- **形态**：Python 3.12 + Streamlit 本地 Web 应用（WSL 中运行，Windows 浏览器访问 localhost:8501）
- **LLM**：Anthropic API（走中转站 `ai.deepthink.works`），负责挖场景、写生图提示词、看图写文案
- **生图**：OpenAI 兼容接口（走网关 `ai-gateway.deepthink.works/v1`），默认 `gpt-image-1`
- **启动**：`~/venvs/meta-creative-tool/bin/streamlit run app.py`（venv 在 WSL 原生磁盘，见技术决策 8）

## 模块说明

| 路径 | 职责 |
|------|------|
| `app.py` | 入口，st.navigation 三页导航 |
| `pages_/workflow.py` | 主工作流页：Step0 输入 → Step1 找场景 → Step2 生图提示词 → Step3 生图 → Step4 看图写文案 → 导出 |
| `pages_/settings.py` | 设置页：key（优先环境变量）、模型下拉框（API 实时拉取）、测试连接 |
| `pages_/prompts_editor.py` | 提示词管理页：4 套提示词在线编辑/恢复默认 |
| `core/config.py` | config.json 读写；key 从环境变量兜底（`ENV_FALLBACK`） |
| `core/prompts.py` | 7 套默认提示词、prompts.json 读写、`render()` 变量替换（手动 replace，不用 str.format） |
| `core/llm.py` | Claude 封装：`call_json()`（含看图）、`list_models()`、`test_connection()`、JSON 解析容错 |
| `core/imagen.py` | 生图封装：`generate_image()`（文生图/图生图）、`edit_image()`（成品图编辑：尺寸改版/按意见修改）、4:5 裁切、`list_models()`（关键词筛生图模型） |
| `core/store.py` | 落盘：run 目录、图片命名、manifest.json、交付表.xlsx 导出 |

## 数据与接口协议（未经确认不得变更）

- **config.json**：`anthropic_api_key / anthropic_base_url / claude_model / openai_api_key / openai_base_url / image_model`
- **环境变量**：`ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / OPENAI_API_KEY / OPENAI_BASE_URL`（存于用户 WSL `~/.bashrc`，config.json 留空时生效）
- **prompts.json**：7 个 key：`scene_mining / image_prompt_gen / image_style_template / copywriting / ratio_adapt / refine_text / image_refine`，各含 `name/description/variables/template`
- **manifest.json**：`{product_info, updated_at, jobs: [{main_scene, sub_scene, sub_scene_desc, ratio, image_prompt, filename, image_path, copies, derived_from}]}`（`derived_from`：该图由哪张母版图改尺寸而来，普通图为空串）
- **交付表.xlsx 列**：图片文件名 | 主场景 | 细分场景 | 尺寸 | 文案序号 | 角度 | 标题 Headline | 主文案 Primary Text | 生图提示词
- **LLM 三个环节的 JSON 返回结构**：见 `core/prompts.py` 各模板内的格式约定（`scenes[] / image_prompt / copies[]`）

## 已完成功能

- [x] 四步工作流全流程（找场景→提示词→生图→文案→导出），每步可人工编辑干预
- [x] 尺寸选择：1:1 / 4:5 / 双尺寸；4:5 通过 1024×1536 生成后居中裁切 1024×1280 实现
- [x] 双尺寸母版派生：先出 4:5 母版，再用「尺寸改版」提示词把成品改成内容一致的 1:1；母版重生成/被修改后派生图自动失效待重做
- [x] 场景结果卡片式多选 UI（按主场景分组，点卡片勾选/取消细分场景）
- [x] 四处持续对话修改：场景 / 生图提示词 / 图片（原图基础上重绘，可回退上一版）/ 文案，入参 = 当前结果 + 修改意见 + 历史意见
- [x] 生图并发：独立图全部并行提交、不设并发上限（限流交给 API 侧 + 重试退避），派生图依赖母版串行
- [x] 图生图：上传产品参考图走 `images.edit`
- [x] 单张图重新生成
- [x] 7 套提示词在线编辑/恢复默认
- [x] key 环境变量方案 + 设置页不落盘 key
- [x] 模型下拉框（Claude 侧 + 生图侧实时拉取，支持手动输入兜底）
- [x] 导出交付包（图片 + manifest.json + 交付表.xlsx）
- [x] 连通性测试全部通过（Claude 中转站 + 生图网关真实出图验证）

## 当前开发计划

- [ ] 非 OpenAI 系生图模型的尺寸适配（seedream 原生支持 4:5、gemini-image 参数不同），换模型前需适配
- [ ] outputs 历史 run 的浏览/恢复页面（manifest 已落盘，UI 未做）
- [ ] 待用户提出

## 重要技术决策

1. **中转站 WAF 绕过**：`ai-gateway.deepthink.works` 的 Cloudflare 会拦 OpenAI SDK 默认 User-Agent，`core/imagen.py::get_client` 和 `core/llm.py::list_models` 兜底分支统一设 `User-Agent: curl/8.5.0`。**不得移除**。
2. **4:5 尺寸实现**：gpt-image-1 原生无 4:5，采用 1024×1536 生成 + Pillow 居中裁切 1024×1280。若换支持原生 4:5 的模型需在 `RATIO_SPECS` 做模型级适配。
3. **提示词模板渲染**：用逐变量 `str.replace`，不用 `str.format`——模板里含 JSON 示例花括号。
4. **key 管理**：环境变量优先（`load_config(env_fallback=True)`），设置页读写原始 config.json（`env_fallback=False`），避免 env key 落盘。
5. **页面目录叫 `pages_`**（带下划线）：避免触发 Streamlit 旧版自动多页机制，导航由 `st.navigation` 显式声明。
6. **LLM JSON 输出**：靠提示词约定 + `_extract_json()` 容错解析（剥代码围栏/前后杂文），未用 structured outputs（需兼容中转站）。
7. **依赖版本**：anthropic 1.x（基于 httpx2）、openai 3.x、streamlit 1.62。venv 用 `--without-pip` + get-pip.py 创建（WSL 无 python3-venv 包且无 sudo）。
8. **venv 必须放 WSL 原生磁盘**（`~/venvs/meta-creative-tool`），不能放项目目录：项目在 /mnt/c（9p 文件系统），venv 放那里冷启动 import 需 35 秒+，WSL 侧仅 1 秒。同理 `.streamlit/config.toml` 设了 `fileWatcherType = "none"`。**不要把 venv 建回项目目录**。
9. **双尺寸 = 母版派生而非独立双生成**：4:5 为母版，1:1 用 `ratio_adapt` 提示词 + `imagen.edit_image()`（输入为母版成品图）改尺寸得到，保证两个尺寸内容一致。job 的 `derived_from` 字段记录母版文件名；母版图变化时派生图必须失效。注意 `images.edit` 是重绘，内容"高度一致"而非像素级一致。
10. **对话式修改的实现**：文字类（场景/提示词/文案）统一走 `refine_text` 提示词，要求模型返回 `{"result": <与当前结果同构>}`；图片走 `image_refine` 提示词 + `edit_image()`（原图为输入），保留 `prev_image_bytes` 支持回退一版。对话历史存 session_state（key 前缀 `chat_*`，含 `jobs_gen` 批次号隔离）。
11. **生图并发**：线程池只用于相互独立的母版图，线程内不访问 `st.session_state`（参数在主线程取好再提交）；派生图依赖母版必须串行。按用户要求**不设并发上限**（`max_workers=任务数`），限流由 API 侧兜底 + `generate_image` 自带重试退避。

## 协作规范（Claude 必须遵守）

### 代码修改纪律

1. 修改前先分析现有代码结构（读相关文件，不凭记忆改）
2. **不允许直接重构核心代码**（core/ 下所有模块）
3. **不删除已有功能**
4. **不改变接口协议**（上方"数据与接口协议"一节所列全部内容）
5. **不修改数据结构/存储格式**（config.json、prompts.json、manifest.json、xlsx 列结构），除非用户明确确认
6. 修改前先告知影响范围

### 功能开发流程

所有功能开发，先输出以下三项，**用户确认后**再动代码：
- 实现方案
- 涉及文件
- 风险点

### Git 工作流（代码托管在 GitHub）

1. 所有修改必须基于当前 git 状态（改前先 `git status` / `git branch`）
2. 修改前检查所在 branch
3. **不直接在 main 上提交**，在 `develop` 或 `feature/*` 分支开发
4. 每次完成任务生成 commit 建议（规范：`feat: / fix: / docs: / refactor: 中文描述`）
5. 保持 README.md 和 CLAUDE.md 与代码同步

### 文档维护

- 每完成一个阶段更新本文件的「已完成功能」「当前开发计划」，有新决策补入「重要技术决策」
- 用户可见的使用方式变化同步更新 README.md

## 变更日志

- 2026-08-27：项目初始化——四步工作流、提示词管理、设置页、key 环境变量方案、模型下拉框、连通性全链路测通；建立 git 仓库与本规范。
- 2026-08-27：修复页面加载慢（35s+→秒级）——venv 迁至 WSL 原生磁盘 `~/venvs/meta-creative-tool`，关闭文件监听（决策 8）。
- 2026-08-27：三项功能升级——①双尺寸改为母版派生（4:5 成品改尺寸出 1:1，内容一致，新增 `ratio_adapt` 提示词与 `imagen.edit_image()`）；②场景卡片 UI + 场景/提示词/图片/文案四处持续对话修改（新增 `refine_text`、`image_refine` 提示词）；③生图并发。提示词 4→7 套，manifest job 新增 `derived_from` 字段。
- 2026-08-27：按用户反馈调整——场景卡片改回多选（点卡片勾选/取消）；生图并发不设上限（去掉 `image_concurrency` 配置项，限流交给 API 侧）。
