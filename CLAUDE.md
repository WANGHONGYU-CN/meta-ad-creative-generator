# CLAUDE.md — Meta 素材工厂

> 本文件是项目的长期维护档案与协作规范。**每完成一个开发阶段必须更新本文件**，并保持与 README.md 同步。

## 项目目标

为 Meta（Facebook/Instagram）投放团队提供素材生产流水线工具：
**产品信息 → AI 挖掘投放场景（主场景+细分场景，含海报文案与评分）→ 场景数据直连生图提示词模板 → 生图（1:1 / 4:5 / 双尺寸）→ AI 看图写多套标题文案并与图片绑定 → 导出交付包**。

定位：**生产项目，长期维护**，不是一次性脚本。使用者为投放团队成员，本地运行。

## 技术架构

- **形态**：Python 3.12 + Streamlit 本地 Web 应用（WSL 中运行，Windows 浏览器访问 localhost:8501）
- **LLM**：Anthropic API（走中转站 `ai.deepthink.works`），负责挖场景、写生图提示词、看图写文案
- **生图**：OpenAI 兼容接口（走网关 `ai-gateway.deepthink.works/v1`），默认 `gpt-image-1`
- **启动**：`~/venvs/meta-creative-tool/bin/streamlit run app.py`（venv 在 WSL 原生磁盘，见技术决策 8）

## 模块说明

| 路径 | 职责 |
|------|------|
| `app.py` | 入口，st.navigation 五页导航 |
| `pages_/workflow.py` | 主工作流页（多任务工作台）：Step0 输入 → Step1 找场景 → Step2 生成生图提示词（场景变量→Claude）→ Step3 生图 → Step4 看图写文案 → 导出；侧边栏任务切换，耗时环节后台运行 |
| `core/runstate.py` | 每任务 state.json 读写（每 run 锁内读改写）、从 manifest 重建老任务、参考图/回退图文件管理 |
| `core/tasks.py` | 后台任务执行器：进程级单例线程池 + 状态表，三条管线（批量提示词/生图/批量文案），结果经 runstate 逐条落盘 |
| `pages_/settings.py` | 设置页：key（优先环境变量）、模型下拉框（API 实时拉取）、测试连接 |
| `pages_/prompts_editor.py` | 提示词管理页：6 套提示词在线编辑/恢复默认，按 主流程（挖场景/生图/文案）与 分支（改尺寸/结果修改/图片修改）分组 |
| `core/config.py` | config.json 读写；key 从环境变量兜底（`ENV_FALLBACK`） |
| `core/prompts.py` | 6 套默认提示词、prompts.json 读写、`render()` 变量替换（手动 replace，不用 str.format） |
| `core/llm.py` | Claude 封装：`call_json()`（含看图）、`list_models()`、`test_connection()`、JSON 解析容错 |
| `core/imagen.py` | 生图封装：`generate_image()`（文生图/图生图）、`edit_image()`（成品图编辑：尺寸改版/按意见修改）、4:5 裁切、`list_models()`（关键词筛生图模型） |
| `core/store.py` | 落盘：run 目录、图片命名、manifest.json（同步写 SQLite 索引）、交付表.xlsx 导出 |
| `core/logger.py` | 统一日志：`logs/app.log` 按天滚动保留 14 天（gitignore）；llm/imagen/tasks/db/runstate 均已埋点，日志自身故障降级为丢弃不影响主流程 |
| `core/db.py` | SQLite 索引层（data/app.db）：runs/jobs/copies 三表、`sync_run_safe()` 双写、`list_runs()` 检索、`rebuild_from_outputs()` 全量重建 |
| `pages_/history.py` | 历史素材页：搜索/浏览所有 run（图片、提示词、文案），含重建索引按钮、载入到工作流 |
| `pages_/scene_library.py` | 场景库页：历史场景汇总，筛选器（关键词/产品/主场景/总分/出图/投放）、手动投放标签、删除、勾选场景创建新生图任务 |
| `scripts/rebuild_db.py` | 命令行全量重建索引库（首次迁移历史数据 / 库损坏修复） |

## 数据与接口协议（未经确认不得变更）

- **config.json**：`anthropic_api_key / anthropic_base_url / claude_model / openai_api_key / openai_base_url / image_model`
- **环境变量**：`ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / OPENAI_API_KEY / OPENAI_BASE_URL`（存于用户 WSL `~/.bashrc`，config.json 留空时生效）
- **prompts.json**：6 个 key：`scene_mining / image_prompt_gen / copywriting / ratio_adapt / refine_text / image_refine`（主流程 3 + 分支 3；`image_style_template` 已于 2026-08-28 移除），各含 `name/description/variables/template`。`scene_mining` 变量为 `product_info + excluded_scenes`，返回 13 字段细分场景结构（audience/trigger/pain_or_desire/product_use/video_purpose/visual_brief/headline_angle/selling_point/headline/subheadline/cta/score_breakdown/total_score）；解析兼容旧版 name/description 结构。`image_prompt_gen` 为**提示词生成的元提示词**（10 个变量：product_info/main_scene/sub_scene/audience/selling_point/visual_brief/aspect_ratio/headline/subheadline/cta，全部来自场景挖掘输出），Claude 返回 `{"image_prompt": ...}`（英文海报提示词，广告文字原文引用）；生图环节直接发送该提示词，无风格外壳
- **manifest.json**：`{product_info, updated_at, jobs: [{main_scene, sub_scene, sub_scene_desc, ratio, image_prompt, filename, image_path, copies, derived_from}]}`（`derived_from`：该图由哪张母版图改尺寸而来，普通图为空串）
- **state.json**（每 run 目录一份）：工作流完整可恢复状态 `{product_info, ratio_choice, title_count, scenes, selected_scenes, jobs, jobs_gen, ref_images, style_images, logo_images, chats}`（`style_images`/`logo_images` 为 2026-08-28 经确认新增，老任务缺省视为空）；jobs 在 manifest 字段之外多 `rev/has_prev` 运行时字段。manifest 由 state 按字段白名单派生（`runstate.MANIFEST_JOB_KEYS`），协议不受影响
- **交付表.xlsx 列**：图片文件名 | 主场景 | 细分场景 | 尺寸 | 文案序号 | 角度 | 标题 Headline | 主文案 Primary Text | 生图提示词
- **LLM 三个环节的 JSON 返回结构**：见 `core/prompts.py` 各模板内的格式约定（`scenes[] / image_prompt / copies[]`）

## 已完成功能

- [x] 四步工作流全流程（找场景→生成提示词→生图→文案→导出，分支：改尺寸/结果修改/图片修改），每步可人工编辑干预
- [x] 尺寸选择：1:1 / 4:5 / 双尺寸；4:5 通过 1024×1536 生成后居中裁切 1024×1280 实现
- [x] 双尺寸母版派生：先出 4:5 母版，再用「尺寸改版」提示词把成品改成内容一致的 1:1；母版重生成/被修改后派生图自动失效待重做
- [x] 场景结果卡片式多选 UI（按主场景分组，点卡片勾选/取消细分场景）
- [x] 四处持续对话修改：场景 / 生图提示词 / 图片（原图基础上重绘，可回退上一版）/ 文案，入参 = 当前结果 + 修改意见 + 历史意见
- [x] 生图并发：独立图全部并行提交、不设并发上限（限流交给 API 侧 + 重试退避），派生图依赖母版串行
- [x] 图生图：上传产品参考图走 `images.edit`
- [x] 海报风格参考图 + 品牌 Logo：Step0 三个上传位（产品图/风格图/Logo），按任务落盘（`refs/`、`refs_style/`、`refs_logo/`）；生图时按「产品→风格→Logo」固定顺序传参考图，并在发送瞬间追加参考图身份英文说明（要求原样放 Logo、贴近风格图气质），job.image_prompt 本身不变
- [x] 单张图重新生成
- [x] 6 套提示词在线编辑/恢复默认（管理页按 主流程/分支功能 分组）
- [x] key 环境变量方案 + 设置页不落盘 key
- [x] 模型下拉框（Claude 侧 + 生图侧实时拉取，支持手动输入兜底）
- [x] 导出交付包（图片 + manifest.json + 交付表.xlsx）
- [x] SQLite 索引库 + 历史素材页：manifest 落盘时双写入库，可按产品/场景搜索历史 run，支持全量重建导入老数据
- [x] 多任务工作台：任务状态持久化（state.json），侧边栏新建/切换任务，历史页「载入到工作流继续编辑」（老 run 无 state.json 时从 manifest 重建）
- [x] 后台任务：批量提示词 / 生图 / 批量文案 三个耗时环节后台线程运行，期间该任务页面锁定并轮询进度，可切到其它任务继续工作；结果逐条落盘，失败项列表展示可重试
- [x] 场景挖掘提示词 V2：内部 30 候选去重评分后**全部输出**（2026-08-28 起取消 ≥90 分门槛，评分仅供筛选参考）、细分场景含海报文案字段（selling_point/headline/subheadline/cta）+ 五维评分、`excluded_scenes` 历史去重（自动取场景库同产品场景名）
- [x] Step1 场景结果筛选器：主场景多选 + 最低综合评分滑条，只影响卡片展示不影响勾选状态；无评分的老场景在分数筛选 >0 时被隐藏（与场景库页一致）
- [x] 生图提示词生成（Step2）：场景挖掘的全部变量喂给 Claude 产出含广告语的英文海报提示词，可编辑/对话修改后再生图；生图直接发送该提示词（无风格外壳，`image_style_template` 已移除）
- [x] 场景分类库：挖掘结果自动入库（scene_lib 表，产品+主场景+细分场景去重）；出图成功系统自动打 `has_image` 标签、`in_ads` 投放标签手动勾选；筛选器（关键词/产品/主场景/总分区间/出图/投放/排序）；局部删除；勾选场景一键创建独立生图任务（不影响运行中任务，直接从 Step 2 开始）
- [x] 日志系统：`logs/app.log` 按天滚动，覆盖 Claude 调用（模型/耗时/拒绝/JSON 解析失败）、生图（重试/最终失败）、后台任务生命周期与失败项、SQLite 入库失败、state.json 读取失败
- [x] 连通性测试全部通过（Claude 中转站 + 生图网关真实出图验证）

## 当前开发计划

- [ ] 非 OpenAI 系生图模型的尺寸适配（seedream 原生支持 4:5、gemini-image 参数不同），换模型前需适配
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
12. **任务状态的权威数据是 state.json**：manifest.json 由它按白名单派生（协议不变），所有 state 写入必须走 `runstate.persist()/update()`（每 run 进程内锁），UI 与后台线程共用这把锁。**本任务后台运行期间 UI 不得 persist**（`persist()` 内已有 `bg.is_running` 守卫，不要绕过），否则会用旧内存副本覆盖后台结果。
13. **后台任务模型**：`core/tasks.py` 模块级单例线程池（页面重跑不重建）；线程入参在主线程取好、线程内零 session_state；每 run 同时只跑一个后台任务（重复提交被拒绝）；UI 在任务运行时锁定该任务编辑区（`st.fragment(run_every=2s)` 轮询 + `st.stop()`），完成后从盘重载 state 收割。「后台」= 不阻塞页面、可切任务，Streamlit 进程关闭则任务终止（已完成子项已落盘）。
14. **场景库（scene_lib 表）**：场景挖掘/对话修改成功后自动 upsert（唯一键 产品+主场景+细分场景），重复入库更新内容但**保留 has_image / in_ads 标签**；`has_image` 由前台 `_apply_new_image_ss` 和后台 `tasks._apply_image` 两条出图路径自动打标（按 产品+主/细分场景名匹配，失败只记日志）；`excluded_scenes` 按产品信息**全等匹配**取库内场景名（产品文案改字则匹配不到，已知取舍）。场景行新增 `detail` 字段存 9 字段完整结构（state.json 内部格式，非 manifest 协议）；老场景无 detail/分数，按分数筛选时会被过滤。
15. **生图提示词链路（V3，2026-08-28 定稿）**：场景变量 →（`tasks._prompt_vars` 组装，老场景逐项回退 description/headline_angle）→ `image_prompt_gen` 元提示词 → Claude 产出英文海报提示词（广告文字原文引用）→ 生图模型**原样接收**（无风格外壳，`image_style_template` 已删除）。历史沿革：V1=LLM 生成+风格外壳；V2=模板直连无 LLM（当天被 V3 取代）。海报含文字（标题/CTA），生图模型渲染文字可能出现拼写错误，属模型能力边界。**例外（2026-08-28 经确认）**：任务带风格参考图或 Logo 时，`tasks._ref_bundle` 会在发送瞬间在提示词末尾追加一段参考图身份英文说明（不追加模型无法区分多张参考图各是什么）；只有产品参考图或无参考图时仍严格原样直发，job.image_prompt 任何情况下不被改写。Logo 由生图模型**重绘还原**而非像素级贴图，复杂小字 Logo 可能有细节偏差。
16. **SQLite 是索引层，manifest.json 仍是权威数据**：`data/app.db`（已 gitignore）由 `store.save_manifest()` 双写产生，任何时候可用 `db.rebuild_from_outputs()` 从 outputs/ 全量重建（幂等：按 dir_name upsert run、整删整插 jobs）。入库失败**不得中断素材生产**（`sync_run_safe` 吞异常返回错误串）。改 db schema 无迁移负担——直接删库重建。若 /mnt/c 上 SQLite 出现锁异常/变慢，把 `DB_PATH` 迁到 WSL 原生盘。

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
4. 每次完成任务生成 commit 建议（规范：`feat: / fix: / docs: / refactor: 中文描述`），**并在用户确认功能可用后当场提交**——不要多个功能堆积在工作区（2026-08-28 曾堆积 6 个功能一次性提交，历史无法按功能拆分，引以为戒）
5. 保持 README.md 和 CLAUDE.md 与代码同步

### 文档维护

- 每完成一个阶段更新本文件的「已完成功能」「当前开发计划」，有新决策补入「重要技术决策」
- 用户可见的使用方式变化同步更新 README.md

## 变更日志

- 2026-08-27：项目初始化——四步工作流、提示词管理、设置页、key 环境变量方案、模型下拉框、连通性全链路测通；建立 git 仓库与本规范。
- 2026-08-27：修复页面加载慢（35s+→秒级）——venv 迁至 WSL 原生磁盘 `~/venvs/meta-creative-tool`，关闭文件监听（决策 8）。
- 2026-08-27：三项功能升级——①双尺寸改为母版派生（4:5 成品改尺寸出 1:1，内容一致，新增 `ratio_adapt` 提示词与 `imagen.edit_image()`）；②场景卡片 UI + 场景/提示词/图片/文案四处持续对话修改（新增 `refine_text`、`image_refine` 提示词）；③生图并发。提示词 4→7 套，manifest job 新增 `derived_from` 字段。
- 2026-08-27：按用户反馈调整——场景卡片改回多选（点卡片勾选/取消）；生图并发不设上限（去掉 `image_concurrency` 配置项，限流交给 API 侧）。
- 2026-08-27：接入 SQLite 索引库（决策 16）——新增 `core/db.py`、历史素材页 `pages_/history.py`、重建脚本 `scripts/rebuild_db.py`；`store.save_manifest` 增加双写。manifest/xlsx 等既有协议未变。
- 2026-08-28：生图提示词链路定稿为「场景变量→Claude 生成→直发生图」（决策 15，V3）——恢复 Step2 提示词生成环节但输入升级为场景全部变量（含广告文字），`image_prompt_gen` 换成可在管理页编辑的元提示词；此前同日曾短暂改为模板直连（V2）并删除 `image_style_template`（7→6 套，保持删除）。原直连变更记录：——删除原 Step2「LLM 生成提示词」环节，`image_prompt_gen` 换成用户的海报模板（场景字段直接填入、直发生图模型）；scene_mining 增补 selling_point/headline/subheadline/cta 四个海报文案字段；`image_style_template` 改直通；工作流改为选场景→一键生图（Step 重编号：生图=Step2、文案=Step3）。随后按用户确认删除 `image_style_template`（提示词 7→6 套，生图管线直发 job.image_prompt），提示词管理页按主流程/分支分组。
- 2026-08-28：场景挖掘提示词 V2 + 场景分类库（决策 14）——scene_mining 换 9 字段评分版模板（新增 excluded_scenes 变量）；新增 scene_lib 表与场景库页（筛选器/自动出图标签/手动投放标签/删除/勾选建生图任务）；Step2 输入升级为 visual_brief 组合文本。manifest 协议不变。
- 2026-08-28：日志系统——新增 `core/logger.py`（logs/app.log 按天滚动 14 天），llm/imagen/tasks/db/runstate 追加式埋点，不改任何逻辑。
- 2026-08-27：多任务工作台 + 后台任务（决策 12/13）——新增 `core/runstate.py`（state.json）、`core/tasks.py`（后台三管线）；workflow.py 改造为任务制（侧边栏切换、后台运行时锁定轮询）；历史页支持载入老 run 继续编辑；协议新增 state.json（manifest 协议不变）。
- 2026-08-28：Step1 场景挖掘取消内部分数淘汰——scene_mining 模板改为候选去重评分后全部输出（同步更新 prompts.json 已保存模板），Step1 结果区新增筛选器（主场景多选 + 最低综合评分滑条，仅影响展示）。返回结构与各协议不变。
- 2026-08-28：海报风格参考图 + 品牌 Logo（决策 15 例外条款）——Step0 新增风格图/Logo 上传位，state.json 经确认新增 `style_images`/`logo_images` 两个 key；生图时按固定顺序传参考图并追加身份说明（`tasks._ref_bundle`），仅产品图时行为不变。
