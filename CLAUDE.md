# CLAUDE.md — Meta 素材工厂

> 本文件是项目的长期维护档案与协作规范。**每完成一个开发阶段必须更新本文件**，并保持与 README.md 同步。

## 项目目标

为 Meta（Facebook/Instagram）投放团队提供素材生产流水线工具：
**产品信息（含品牌名/广告语言）→ AI 挖掘投放场景（主场景+细分场景，含评分）→ 场景变量直填「生图总提示词」模板 → 生图（1:1 / 4:5 / 双尺寸）→ AI 看图写多套标题文案并与图片绑定 → 导出交付包**。

定位：**生产项目，长期维护**，不是一次性脚本。使用者为投放团队成员，本地运行。

## 技术架构

- **形态**：Python 3.12 + Streamlit 本地 Web 应用（WSL 中运行，Windows 浏览器访问 localhost:8501）；本分支（feature/web-rewrite）已完成 FastAPI + React 正式 Web 架构（阶段一 API 层 `server/` + 阶段二前端 `web/`，启动 `uvicorn server.main:app --port 8000` 后浏览器直接访问 8000 端口即 Web 界面；**必须单进程**，迁移期与 Streamlit 同一时间只开一边）
- **LLM**：Anthropic API（走中转站 `ai.deepthink.works`），负责挖场景、看图写文案、对话式修改（生图提示词由模板本地渲染，不经 Claude）
- **生图**：OpenAI 兼容接口（走网关 `ai-gateway.deepthink.works/v1`），默认 `gpt-image-1`
- **启动**：`~/venvs/meta-creative-tool/bin/streamlit run app.py`（venv 在 WSL 原生磁盘，见技术决策 8）

## 模块说明

| 路径 | 职责 |
|------|------|
| `app.py` | 入口，st.navigation 五页导航；含 Step0 输入 keep-alive（决策 17，切页不清空，白名单含 brand_name/ad_language） |
| `pages_/workflow.py` | 主工作流页（多任务工作台）：Step0 输入（产品信息/品牌名/广告语言/参考图/尺寸）→ Step1 找场景 → Step2 生图（场景变量本地直填「生图总提示词」模板，一键出图）→ Step3 看图写文案 → 导出；侧边栏任务切换，耗时环节后台运行 |
| `core/runstate.py` | 每任务 state.json 读写（每 run 锁内读改写）、从 manifest 重建老任务、参考图/回退图文件管理 |
| `core/tasks.py` | 后台任务执行器：进程级单例线程池 + 状态表，两条管线（生图/批量文案）+ 单张图片并发修改通道（`submit_image_edit`，与管线互斥），结果经 runstate 逐条落盘；`new_job()` 供工作流页构建 job |
| `pages_/settings.py` | 设置页：key（优先环境变量）、模型下拉框（API 实时拉取）、测试连接 |
| `pages_/prompts_editor.py` | 提示词管理页：6 套提示词在线编辑/恢复默认，按 主流程（挖场景/生图/文案）与 分支（改尺寸/结果修改/图片修改）分组 |
| `core/config.py` | config.json 读写；key 从环境变量兜底（`ENV_FALLBACK`） |
| `core/prompts.py` | 6 套默认提示词、prompts.json 读写、`render()` 变量替换（手动 replace，不用 str.format） |
| `core/llm.py` | Claude 封装：`call_json()`（含看图）、`list_models()`、`test_connection()`、JSON 解析容错 |
| `core/imagen.py` | 生图封装：`generate_image()`（文生图/图生图）、`edit_image()`（成品图编辑：尺寸改版/按意见修改）、4:5 裁切、`list_models()`（关键词筛生图模型） |
| `core/assets.py` | 全局参考图库（data/ref_assets/{style,logo}，gitignore）：上传时按内容 sha256 去重收录、首次自动导入历史任务参考图、Step0「从历史图选择」数据源；删库内图不影响任务（任务持有自己的副本） |
| `core/store.py` | 落盘：run 目录、图片命名、manifest.json（同步写 SQLite 索引）、交付表.xlsx 导出 |
| `core/logger.py` | 统一日志：`logs/app.log` 按天滚动保留 14 天（gitignore）；llm/imagen/tasks/db/runstate 均已埋点，日志自身故障降级为丢弃不影响主流程 |
| `core/db.py` | SQLite 索引层（data/app.db）：runs/jobs/copies 三表、`sync_run_safe()` 双写、`list_runs()` 检索、`rebuild_from_outputs()` 全量重建 |
| `pages_/history.py` | 历史素材页：搜索/浏览所有 run（图片、提示词、文案），含重建索引按钮、载入到工作流 |
| `pages_/scene_library.py` | 场景库页：历史场景汇总，筛选器（关键词/产品/主场景/总分/出图/投放）、手动投放标签、删除、勾选场景创建新生图任务 |
| `scripts/rebuild_db.py` | 命令行全量重建索引库（首次迁移历史数据 / 库损坏修复） |
| `server/main.py` | Web API 入口（FastAPI）：CORS、六组路由、/files 静态文件（outputs 与参考图库）、web/dist 存在时托管前端构建产物 |
| `server/routers/` | 路由层：runs（任务 CRUD/状态/导出）、workflow_ops（挖场景/生图/图片修改与版本/文案/各处对话修改）、refs（参考图+图库）、prompts_admin、settings_admin、library（历史+场景库） |
| `server/services/workflow.py` | 工作流业务逻辑（从 pages_/workflow.py 抽取，无 UI 依赖）：提示词渲染、build_jobs、refine 系列、生图/文案提交、版本回跳、导出 zip；状态修改全走 runstate.update 锁内字段级读改写 |
| `server/services/mining.py` | 场景挖掘后台通道（API 版）：线程池 + 状态表，与 core/tasks.py 管线互斥，轮询语义与生图管线一致 |
| `server/services/scene_lib.py` | 场景库建任务 + 同产品参考图/品牌名继承（从 pages_/scene_library.py 抽取） |
| `server/deps.py` | 路由公共件：run 名校验（防路径穿越）、state 读取、文件 URL 拼装（image_url 带 rev 缓存戳）、组合状态轮询 |
| `web/` | React 前端（Vite + TypeScript + Ant Design v5）：`src/api/`（请求封装/类型/轮询与后台收割 hooks）、`src/layout/AppShell`（侧边导航+顶栏任务切换）、`src/pages/Workspace/`（分步面板：产品信息/场景/图片/文案导出）、`src/pages/`（场景库/历史/提示词/设置）、`src/components/ChatDrawer`（通用 AI 修改对话）。构建产物 `web/dist` **有意入库**（pull 即用无需 Node）；`web/node_modules` 是指向 WSL 原生盘的符号链接（决策 8 同理），已 gitignore |

## 数据与接口协议（未经确认不得变更）

- **config.json**：`anthropic_api_key / anthropic_base_url / claude_model / openai_api_key / openai_base_url / image_model`
- **环境变量**：`ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / OPENAI_API_KEY / OPENAI_BASE_URL`（存于用户 WSL `~/.bashrc`，config.json 留空时生效）
- **prompts.json**：6 个 key：`scene_mining / image_gen / copywriting / ratio_adapt / refine_text / image_refine`（主流程 3 + 分支 3；`image_prompt_gen` 已于 2026-09-01 经确认替换为 `image_gen`，`image_style_template` 已于 2026-08-28 移除），各含 `name/description/variables/template`。`scene_mining` 变量为 `product_info + excluded_scenes`，返回 6 字段细分场景结构（audience/trigger/pain_or_desire/product_use/score_breakdown/total_score，2026-09-01 起 visual_brief/headline_angle/video_purpose/selling_point/headline/subheadline/cta 经确认不再输出）；解析兼容旧版 name/description 结构。`image_gen` 为**直发生图模型的中文总提示词模板**（10 个变量：reference_style_image/main_scene/sub_scene/audience/trigger/pain_or_desire/product_use/aspect_ratio/ad_language/brand_name），场景变量在工作流页本地 render（不调 Claude），渲染结果即 job.image_prompt；`{reference_style_image}` 占位符保留到发送瞬间由生图管线按是否带风格图填充
- **manifest.json**：`{product_info, updated_at, jobs: [{main_scene, sub_scene, sub_scene_desc, ratio, image_prompt, filename, image_path, copies, derived_from}]}`（`derived_from`：该图由哪张母版图改尺寸而来，普通图为空串）
- **state.json**（每 run 目录一份）：工作流完整可恢复状态 `{product_info, brand_name, ad_language, ratio_choice, title_count, scenes, selected_scenes, jobs, jobs_gen, ref_images, style_images, logo_images, chats}`（`brand_name`/`ad_language` 为 2026-09-01 经确认新增，老任务缺省视为空串；`style_images`/`logo_images` 为 2026-08-28 经确认新增，老任务缺省视为空；`ref_images` key 保留但 Step0 产品参考图上传位已于 2026-08-31 移除——新任务恒为空，老任务遗留值生图时仍生效）；jobs 在 manifest 字段之外多 `rev/has_prev/hist/hist_idx/hist_seq` 运行时字段（后三个为 2026-09-01 图片版本历史新增）。manifest 由 state 按字段白名单派生（`runstate.MANIFEST_JOB_KEYS`），协议不受影响
- **交付表.xlsx 列**：图片文件名 | 主场景 | 细分场景 | 尺寸 | 文案序号 | 角度 | 标题 Headline | 主文案 Primary Text | 生图提示词
- **LLM 两个环节的 JSON 返回结构**：见 `core/prompts.py` 各模板内的格式约定（`scenes[] / copies[]`；生图提示词自 2026-09-01 起为本地模板渲染，无 LLM JSON 环节）

## 已完成功能

- [x] 工作流全流程（找场景→变量直填一键生图→文案→导出，分支：改尺寸/结果修改/图片修改），每步可人工编辑干预
- [x] 尺寸选择：1:1 / 4:5 / 双尺寸；4:5 通过 1024×1536 生成后居中裁切 1024×1280 实现
- [x] 双尺寸母版派生：先出 4:5 母版，再用「尺寸改版」提示词把成品改成内容一致的 1:1；母版重生成/被修改后派生图自动失效待重做
- [x] 场景结果卡片式多选 UI（按主场景分组，点卡片勾选/取消细分场景）
- [x] 四处持续对话修改：场景 / 生图提示词 / 图片 / 文案，入参 = 当前结果 + 修改意见 + 历史意见；图片修改走**后台并发通道**（多张图同时改互不阻塞、只锁被改的图卡片，2026-09-01）
- [x] 图片版本历史：每张图保留最近 10 版（`images/.hist/`），◀ 上一版/下一版 ▶ + 历史缩略图任意回跳；回退到旧版后再修改丢弃后面的版本；老图的 `.prev` 单版回退首次修改时自动收编
- [x] 点击提速：勾选场景等高频操作只打脏标记（落盘收敛到关键节点 + 15 秒兜底），场景卡片区 st.fragment 局部重跑不整页刷新
- [x] 场景精简模式（Step1 默认开）：每主场景组只显示综合评分前 30%（至少 2 个，已勾选恒显示），一键展开全部
- [x] 生图并发：独立图全部并行提交、不设并发上限（限流交给 API 侧 + 重试退避），派生图依赖母版串行
- [x] 图生图：参考图走 `images.edit`（产品参考图上传位已于 2026-08-31 经用户确认移除，老任务已保存的产品参考图生图时仍生效）
- [x] 海报风格参考图 + 品牌 Logo：Step0 两个上传位（风格图/Logo；产品图上传位已移除），按任务落盘（`refs_style/`、`refs_logo/`）；生图时按「产品(老任务遗留)→风格→Logo」固定顺序传参考图，并在发送瞬间追加参考图身份英文说明（要求原样放 Logo、贴近风格图气质），job.image_prompt 本身不变
- [x] Step0 输入切页不丢：keep-alive 保活（product_info/brand_name/ad_language/ratio_choice/title_count，决策 17）；参考图上传后立即落盘、任务未建时先暂存内存建任务后补存（暂存跨任务切换不清空）；上传框下方缩略图回显当前生效的图并支持单张删除（file_uploader 本体无法程序回填，属平台限制）
- [x] 参考图历史库：上传的风格图/Logo 自动按内容去重收录进全局图库（`core/assets.py`），Step0 上传框下方「📚 从历史图选择」缩略图勾选后一键应用（整组替换，风格图多选/Logo 单选），可从库中删除；首次启用自动导入所有历史任务的参考图
- [x] 场景库建任务自动继承参考图与品牌名/广告语言：勾场景创建生图任务时，自动复制同产品（product_info 全等）最近一个带风格图/Logo 任务的参考图，并继承最近填过的 brand_name/ad_language；Step2 生图按钮旁任务不带任何参考图会显式警告「将纯文生图」
- [x] 单张图重新生成
- [x] 6 套提示词在线编辑/恢复默认（管理页按 主流程/分支功能 分组）
- [x] key 环境变量方案 + 设置页不落盘 key
- [x] 模型下拉框（Claude 侧 + 生图侧实时拉取，支持手动输入兜底）
- [x] 导出交付包（图片 + manifest.json + 交付表.xlsx）
- [x] SQLite 索引库 + 历史素材页：manifest 落盘时双写入库，可按产品/场景搜索历史 run，支持全量重建导入老数据
- [x] 多任务工作台：任务状态持久化（state.json），侧边栏新建/切换任务，历史页「载入到工作流继续编辑」（老 run 无 state.json 时从 manifest 重建）
- [x] 后台任务：生图 / 批量文案 两个耗时环节后台线程运行（批量提示词管线已于 2026-09-01 随 V4 链路移除），期间该任务页面锁定并轮询进度，可切到其它任务继续工作；结果逐条落盘，失败项列表展示可「补齐/重试」
- [x] 场景挖掘提示词 V3（2026-09-01）：内部 30 候选去重评分后**全部输出**（评分仅供筛选参考）、细分场景 6 字段（audience/trigger/pain_or_desire/product_use + 五维评分，海报文案与画面 brief 字段经确认移除）、`excluded_scenes` 历史去重（自动取场景库同产品场景名）
- [x] Step1 场景结果筛选器：主场景多选 + 最低综合评分滑条，只影响卡片展示不影响勾选状态；无评分的老场景在分数筛选 >0 时被隐藏（与场景库页一致）
- [x] 生图链路 V4（Step2，2026-09-01）：勾选场景一键生图——场景变量 + 品牌名/广告语言/比例 本地填入 `image_gen` 中文总提示词模板（不调 Claude），渲染结果即最终提示词直发生图模型；每张图提示词可展开编辑/对话修改后单张重生成；海报标题/副标题由生图模型按模板要求自行撰写
- [x] 场景分类库：挖掘结果自动入库（scene_lib 表，产品+主场景+细分场景去重）；出图成功系统自动打 `has_image` 标签、`in_ads` 投放标签手动勾选；筛选器（关键词/产品/主场景/总分区间/出图/投放/排序）；局部删除；勾选场景一键创建独立生图任务（不影响运行中任务，进工作流直接点生图）。操作按钮（创建生图任务/删除）**常驻左侧边栏**（实时显示已选数量，滚动到哪都可点），页面底部保留一份
- [x] 日志系统：`logs/app.log` 按天滚动，覆盖 Claude 调用（模型/耗时/拒绝/JSON 解析失败）、生图（重试/最终失败）、后台任务生命周期与失败项、SQLite 入库失败、state.json 读取失败
- [x] 连通性测试全部通过（Claude 中转站 + 生图网关真实出图验证）

## 当前开发计划

- [ ] **Web 化阶段三**：用户双轨验收（Web 版走完整流程与 Streamlit 对照）后合并 main；确认稳定后移除 Streamlit 入口
- [x] Web 化阶段二：React 前端（Vite + TS + AntD，交互重新设计非复刻），构建产物 web/dist 由 FastAPI 托管
- [ ] 非 OpenAI 系生图模型的尺寸适配（seedream 原生支持 4:5、gemini-image 参数不同），换模型前需适配；gpt-image-2 已于 2026-09-03 适配（决策 2）
- [ ] 待用户提出

## 重要技术决策

1. **中转站 WAF 绕过**：`ai-gateway.deepthink.works` 的 Cloudflare 会拦 OpenAI SDK 默认 User-Agent，`core/imagen.py::get_client` 和 `core/llm.py::list_models` 兜底分支统一设 `User-Agent: curl/8.5.0`。**不得移除**。
2. **4:5 尺寸实现（2026-09-03 模型级适配）**：`imagen.ratio_spec(model, ratio)` 按模型分路——模型名含 `gpt-image-2` 时 4:5 直接请求自定义分辨率 1024×1280 **原生生成不裁切**（该系支持任意 16 倍数分辨率、比例 1:3~3:1，已真实出图验证顶/底文字完整）；其它模型（gpt-image-1 等）维持 1024×1536 生成 + Pillow 居中裁切 1024×1280（上下各损失 128px，海报文字可能被切，提示词需自带安全区约束）。新增支持原生 4:5 的模型往 `CUSTOM_SIZE_MODELS` 加关键字即可。
3. **提示词模板渲染**：用逐变量 `str.replace`，不用 `str.format`——模板里含 JSON 示例花括号。
4. **key 管理**：环境变量优先（`load_config(env_fallback=True)`），设置页读写原始 config.json（`env_fallback=False`），避免 env key 落盘。
5. **页面目录叫 `pages_`**（带下划线）：避免触发 Streamlit 旧版自动多页机制，导航由 `st.navigation` 显式声明。
6. **LLM JSON 输出**：靠提示词约定 + `_extract_json()` 容错解析（剥代码围栏/前后杂文），未用 structured outputs（需兼容中转站）。`call_json` 用 `max_tokens=32000` + **流式收取**（SDK 对超 10 分钟的非流式请求直接报错；场景全量输出后回复可达 2 万+ 字符，16000 曾被截断成 JSON 解析错误）；`stop_reason == "max_tokens"` 时报明确的「回复被截断」错误。中转站流式已实测可用。
7. **依赖版本**：anthropic 1.x（基于 httpx2）、openai 3.x、streamlit 1.62。venv 用 `--without-pip` + get-pip.py 创建（WSL 无 python3-venv 包且无 sudo）。
8. **venv 必须放 WSL 原生磁盘**（`~/venvs/meta-creative-tool`），不能放项目目录：项目在 /mnt/c（9p 文件系统），venv 放那里冷启动 import 需 35 秒+，WSL 侧仅 1 秒。同理 `.streamlit/config.toml` 设了 `fileWatcherType = "none"`。**不要把 venv 建回项目目录**。
9. **双尺寸 = 母版派生而非独立双生成**：4:5 为母版，1:1 用 `ratio_adapt` 提示词 + `imagen.edit_image()`（输入为母版成品图）改尺寸得到，保证两个尺寸内容一致。job 的 `derived_from` 字段记录母版文件名；母版图变化时派生图必须失效。注意 `images.edit` 是重绘，内容"高度一致"而非像素级一致。
10. **对话式修改的实现**：文字类（场景/提示词/文案）统一走 `refine_text` 提示词，要求模型返回 `{"result": <与当前结果同构>}`，前台同步执行；图片走 `image_refine` 提示词 + `edit_image()`（原图为输入），**2026-09-01 起改为后台并发通道**（决策 19）。对话历史存 session_state（key 前缀 `chat_*`，含 `jobs_gen` 批次号隔离）。
11. **生图并发**：线程池只用于相互独立的母版图，线程内不访问 `st.session_state`（参数在主线程取好再提交）；派生图依赖母版必须串行。按用户要求**不设并发上限**（`max_workers=任务数`），限流由 API 侧兜底 + `generate_image` 自带重试退避。
12. **任务状态的权威数据是 state.json**：manifest.json 由它按白名单派生（协议不变），所有 state 写入必须走 `runstate.persist()/update()`（每 run 进程内锁），UI 与后台线程共用这把锁。**本任务后台运行期间 UI 不得 persist**（`persist()` 内的守卫 2026-09-01 起为 `bg.is_busy`——管线**或任一图片修改**在跑都跳过，不要绕过），否则会用旧内存副本覆盖后台结果。UI 侧高频操作（勾选场景等）**只打脏标记**（`mark_dirty()`），落盘收敛到关键节点（挖场景/生图/文案/切任务/载入/导出/对话修改后）+「任意重跑时距上次落盘 >15 秒」兜底——每次点击三连写（state.json+manifest+SQLite，均在 /mnt/c 慢盘）曾是点击延迟主因。
13. **后台任务模型**：`core/tasks.py` 模块级单例线程池（页面重跑不重建）；线程入参在主线程取好、线程内零 session_state；每 run 同时只跑一个后台任务（重复提交被拒绝）；UI 在任务运行时锁定该任务编辑区（`st.fragment(run_every=2s)` 轮询 + `st.stop()`），完成后从盘重载 state 收割。「后台」= 不阻塞页面、可切任务，Streamlit 进程关闭则任务终止（已完成子项已落盘）。
14. **场景库（scene_lib 表）**：场景挖掘/对话修改成功后自动 upsert（唯一键 产品+主场景+细分场景），重复入库更新内容但**保留 has_image / in_ads 标签**；`has_image` 由前台 `_apply_new_image_ss` 和后台 `tasks._apply_image` 两条出图路径自动打标（按 产品+主/细分场景名匹配，失败只记日志）；`excluded_scenes` 按产品信息**全等匹配**取库内场景名（产品文案改字则匹配不到，已知取舍）。场景行新增 `detail` 字段存 9 字段完整结构（state.json 内部格式，非 manifest 协议）；老场景无 detail/分数，按分数筛选时会被过滤。
15. **生图提示词链路（V4，2026-09-01 定稿）**：场景变量（audience/trigger/pain_or_desire/product_use，老场景 product_use 回退 description）+ 品牌名/广告语言/比例 →（`workflow._render_job_prompt` 本地 render，瞬时完成不调 Claude）→ `image_gen` 中文总提示词 → 生图模型**原样接收**。海报标题/副标题/CTA 由**生图模型**按模板第四节要求自行撰写（不再由 Claude 预写后原文引用——文字拼写错误概率高于 V3，且每次重生成文字可能不同，属本方案已知取舍）。历史沿革：V1=LLM 生成+风格外壳；V2=模板直连无 LLM（当天被 V3 取代）；V3=场景变量→Claude 写英文提示词→直发（2026-08-28~2026-09-01）。**发送瞬间的两个例外（job.image_prompt 任何情况下不被改写）**：①提示词中的 `{reference_style_image}` 占位符由 `tasks.submit_image_generation` 按当时是否带风格图填成中文说明文字；②任务带风格参考图或 Logo 时，`tasks._ref_bundle` 在末尾追加参考图身份英文说明（不追加模型无法区分多张参考图各是什么）；只有产品参考图或无参考图时不追加。Logo 由生图模型**重绘还原**而非像素级贴图，复杂小字 Logo 可能有细节偏差。
16. **SQLite 是索引层，manifest.json 仍是权威数据**：`data/app.db`（已 gitignore）由 `store.save_manifest()` 双写产生，任何时候可用 `db.rebuild_from_outputs()` 从 outputs/ 全量重建（幂等：按 dir_name upsert run、整删整插 jobs）。入库失败**不得中断素材生产**（`sync_run_safe` 吞异常返回错误串）。改 db schema 无迁移负担——直接删库重建。若 /mnt/c 上 SQLite 出现锁异常/变慢，把 `DB_PATH` 迁到 WSL 原生盘。
17. **Streamlit widget state 切页即回收，Step0 输入靠双保险**（2026-08-31）：带 `key` 的组件在「未被渲染的一次重跑」后 session_state 值被 Streamlit 自动清除（切页必触发）。①文本/选项类：`app.py` 在 `pg.run()` 前对白名单 key（`product_info/brand_name/ad_language/ratio_choice/title_count`）执行 `ss[k] = ss[k]` 保活——重新赋值把 key 标记为用户状态跳过回收，app.py 每次重跑必执行所以任何切页都保得住，**不要把这段循环移进页面脚本**（页面脚本切页时不执行，保活失效）。②file_uploader：内容无法程序回填（平台限制），采用「上传立即接管」——任务已建马上落盘，未建先暂存普通 session key（`_pending_style_images/_pending_logo_images`），建任务后下一次重跑自动补存；上传框下方缩略图回显+单张删除。指纹 key 在切换任务时清理（`_clear_chat_keys`），**暂存 key 不清**——未建任务时上传的图跟人走，载入/新建任务后自动存进该任务（2026-08-31 修复：原先切任务清暂存导致丢图）。
18. **参考图默认按任务隔离，复用靠图库与继承**（2026-08-31）：任务的参考图仍是 run 目录内的私有副本（协议不变）；跨任务复用两条路——①全局参考图库 `data/ref_assets/{style,logo}`（`core/assets.py`，内容 sha256 前 12 位命名去重，首次用 `.backfilled` 标记文件做幂等历史导入），Step0 可视化勾选应用；②场景库建任务时自动继承同产品最近任务的参考图（product_info 全等匹配，与 excluded_scenes 同一取舍；2026-09-01 起 brand_name/ad_language 同机制继承）。库与任务解耦：删库内图不影响任务，删任务不影响库。图库若变慢可整目录迁 WSL 原生盘（同决策 16 的 DB_PATH 思路）。
19. **图片并发修改 + 版本历史（2026-09-01）**：①单张图对话修改走 `tasks.submit_image_edit`——独立于三大管线的轻量后台通道，多张图并发、同一张图拒绝重复提交，**与管线互斥**（`_submit` 与 `submit_image_edit` 互相检查，防止旧下标/旧图互相覆盖）；UI 不锁整页、只锁被改的图卡片，完成后**按 job 从盘合并回内存**（不整页重载，保住页面上其它未落盘改动）。②每张图的版本历史存 `images/.hist/<文件名>/v{seq}.png`，job 运行时字段 `hist/hist_idx/hist_seq` 记链与位置，所有出图路径（管线/前台/修改）统一走 `runstate.apply_image_version()`：首次调用收编现有主图与老 `.prev`、回退中间版再修改会截断后续版本、超 `HIST_LIMIT=10` 删最老；上一步/下一步/任意回跳走 `runstate.goto_image_version()`（前台经 `runstate.update` 锁内改盘再合并回内存，防止与并发修改互踩）。母版换版本/被修改，派生图照旧失效待重做（决策 9）。

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
- 2026-08-28：修复场景挖掘 JSON 截断报错（决策 6 补充）——场景全量输出后回复超过 `max_tokens=16000` 被截断，报「Expecting ',' delimiter」；`call_json` 上限提至 32000 并改流式收取，截断时报明确错误，解析失败日志补记 stop_reason/回复长度。
- 2026-08-31：参考图历史库 + 场景库建任务继承参考图（决策 18）——排查「场景库新建任务生图不带参考图」：参考图按任务隔离，新任务天然为空且无提示，用户以为早上传过的风格图/Logo 会生效。新增 `core/assets.py` 全局图库（上传自动收录+历史导入），Step0 加「从历史图选择」勾选应用；场景库建任务自动继承同产品最近参考图；Step3 无参考图显式警告；修复「未建任务时的上传暂存在切换任务时被清空」的丢图口子。
- 2026-08-31：场景库操作按钮挪到侧边栏常驻——原先只在页面最底部，场景一多就找不到；「创建生图任务/删除」抽成公共渲染函数，侧边栏一份（实时显示已选数量）+ 底部保留一份，逻辑与协议均不变。
- 2026-08-31：Step0 输入切页不丢 + 移除产品参考图上传位（决策 17）——app.py 加 keep-alive 白名单保活 product_info/ratio_choice/title_count；上传图未建任务时先暂存内存、建任务后自动落盘；风格图/Logo 上传框下方缩略图回显+单张删除。产品参考图上传位经用户确认删除（state.json 的 `ref_images` key 保留，老任务遗留值生图时仍生效，协议不变）。
- 2026-08-31：文案环节提速——看图写文案由逐张串行改为全部并行（与生图同策略，`tasks.submit_copywriting`）；发给 Claude 的图片先压成 768px JPEG（新增 `llm.vision_image()`，对话改文案同样生效），原先 3 张图串行发原图 PNG 需 14 分钟。
- 2026-09-01：体验四连改（决策 19 + 决策 12 补充，经用户确认）——①图片对话修改由前台同步（一次只能改一张、整页转圈）改为后台并发通道 `tasks.submit_image_edit`，多张图同时改、只锁被改的卡片；②图片版本历史（`images/.hist/`，上限 10 版）：上一版/下一版 + 历史缩略图任意回跳，老 `.prev` 回退图自动收编；③Step1 场景精简模式（默认开，每组评分前 30%、至少 2 个、已勾选恒显示）；④点击提速：勾选等高频操作改脏标记延迟落盘（关键节点 + 15 秒兜底），场景卡片区改 st.fragment 局部重跑——此前每次点击三连写 /mnt/c 慢盘 + 整页双重跑是延迟主因。
- 2026-09-01：生图链路 V4 + 场景挖掘 V3（决策 15 改版，均经用户确认）——①取消 Step2「Claude 生成提示词」环节：`image_prompt_gen` 替换为 `image_gen`（用户提供的中文生图总提示词，直发生图模型），场景变量+品牌名/广告语言/比例在工作流页本地 render，勾选场景一键生图（后台批量提示词管线删除，`tasks.new_job` 转为公共函数；生图=Step2、文案=Step3 重编号）；②scene_mining 精简为 6 字段（保留五维评分，visual_brief/headline_angle/video_purpose/selling_point/headline/subheadline/cta 移除），海报文字改由生图模型自行撰写；③Step0 新增 品牌名称/广告语言 输入（keep-alive 保活，state.json 经确认新增 `brand_name`/`ad_language` 两个 key），场景库建任务同机制继承；④`{reference_style_image}` 占位符与参考图身份说明均在发送瞬间处理，job.image_prompt 不被改写；prompts.json 已同步重生成。
- 2026-09-03（feature/web-rewrite 分支）：4:5 生图不再裁切（决策 2 适配，经用户确认）——用户反馈生成的图被裁；排查为 4:5 走「1024×1536 生成+居中裁 1024×1280」老路径（gpt-image-1 时代设计），而当前模型 gpt-image-2 支持任意 16 倍数自定义分辨率。`core/imagen.py` 新增 `ratio_spec(model, ratio)`：gpt-image-2 系 4:5 直接原生 1024×1280 出图（真实出图验证顶/底文字完整），其它模型裁切路径不变。
- 2026-09-03（feature/web-rewrite 分支）：Web 化阶段二——React 前端 `web/`（Vite + TypeScript + Ant Design v5），交互按正式产品重新设计（经用户确认「不复刻、要优质交互与简约 UI」）：全局左侧图标导航 + 顶栏任务切换（替代 Streamlit 分页）；工作台改分步面板（产品信息→场景→图片→文案导出，可点击步骤条）；场景区评分徽章卡片 + 筛选工具条 + 底部悬浮操作栏（精简模式默认开）；图片区画廊卡片（重生成/后台并发修改/提示词编辑/版本历史胶片弹窗），后台任务不锁页面（进度横幅 + 2.5s 轮询 + 自动收割 ack）；文案区行内可编辑 + AI 整批修改；导出新增浏览器直接下载 交付包.zip。所有 AI 对话修改统一右侧抽屉（ChatDrawer），历史存 state.json chats 与 Streamlit 互通。FastAPI 增加 SPA 回退路由（刷新前端路由不 404，含路径穿越防护）。web/dist 有意入库、web/node_modules 符号链接到 WSL 原生盘（决策 8 同理）。
- 2026-09-03（feature/web-rewrite 分支）：Web 化阶段一——新增 FastAPI API 层 `server/`（经用户确认的迁移方案：FastAPI + React 前后端分离，core/ 原样复用只重写 UI 层）。pages_/workflow.py 与 scene_library.py 的业务逻辑抽取为 `server/services/`（原页面文件未动，Streamlit 版照常可用）；五个页面的全部操作暴露为 REST 接口（文档 /docs），图片走 /files 静态直出；挖场景在 API 版为后台线程 + 轮询（`services/mining.py`，与生图/文案管线互斥）；对话历史直接存 state.json 的 chats key（沿用 chat_* 命名，与 Streamlit 版互通）；导出新增 交付包.zip 下载接口。数据协议全部未变。真机验收：结构接口全通 + 真实场景挖掘（claude-opus-5，41 场景，249s）落盘/入库/继承链路全通。注意：**uvicorn 必须单进程**；迁移期 Streamlit 与 API 同一时间只开一边（进程内锁不互通）。
