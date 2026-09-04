// 与 server/ 返回结构对应的类型。数据层为 PostgreSQL（run 标识 = run_{id}），
// 这里只声明前端用到的部分，未知字段透传不报错。

export interface Product {
  id: number
  name: string
  info: string
  brand_name: string
  ad_language: string
  run_count?: number
}

export interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}

export interface SceneDetail {
  audience?: string
  trigger?: string
  pain_or_desire?: string
  product_use?: string
  total_score?: number
  score_breakdown?: Record<string, number>
  [k: string]: unknown
}

export interface SceneRow {
  main_scene: string
  sub_scene: string
  description: string
  detail?: SceneDetail
}

export interface Copy {
  angle: string
  headline: string
  primary_text: string
}

export interface Job {
  main_scene: string
  sub_scene: string
  sub_scene_desc: string
  ratio: string
  image_prompt: string
  filename: string
  image_path: string
  copies: Copy[]
  derived_from: string
  rev?: number
  hist?: string[]
  hist_idx?: number
  // 服务端补充的展示字段
  image_url: string
  hist_urls: string[]
}

export interface RunState {
  product_id: number
  product_name: string
  product_info: string
  brand_name: string
  ad_language: string
  ratio_choice: string
  title_count: number
  scenes: SceneRow[]
  selected_scenes: number[]
  jobs: Job[]
  ref_images: string[]
  style_images: string[]
  logo_images: string[]
  chats: Record<string, ChatMsg[]>
  ref_images_urls: string[]
  style_images_urls: string[]
  logo_images_urls: string[]
}

export interface PipelineStatus {
  kind: 'images' | 'copies'
  state: 'running' | 'finished' | 'failed'
  done: number
  total: number
  text: string
  errors: string[]
  consumed: boolean
}

export interface EditStatus {
  state: 'running' | 'finished' | 'failed'
  error: string
}

export interface MiningStatus {
  state: 'running' | 'finished' | 'failed'
  error: string
  count: number
}

export interface RunStatus {
  pipeline: PipelineStatus | null
  edits: Record<string, EditStatus>
  mining: MiningStatus | null
  busy: boolean
}

export interface RunBundle {
  name: string
  state: RunState
  status: RunStatus
}

export interface RunListItem {
  name: string
  product_info: string
  updated_at: string
  job_count: number
  busy: boolean
}

export interface AssetItem {
  path: string
  name: string
  url: string
}

export interface PromptItem {
  name: string
  description: string
  variables: string[]
  template: string
}

export interface PromptsPayload {
  main_keys: string[]
  branch_keys: string[]
  prompts: Record<string, PromptItem>
}

export interface SettingsPayload {
  config: {
    anthropic_api_key: string
    anthropic_base_url: string
    claude_model: string
    openai_api_key: string
    openai_base_url: string
    image_model: string
  }
  env: Record<string, boolean>
  effective_ready: { anthropic: boolean; image: boolean }
}

export interface SceneLibRow {
  id: number
  product_id: number
  product_info: string
  main_scene: string
  sub_scene: string
  description: string
  detail: SceneDetail
  total_score: number | null
  has_image: boolean
  in_ads: boolean
  source_run: string
  created_at: string
}

export interface HistoryRun {
  id: number
  dir_name: string
  product_info: string
  updated_at: string
  job_count: number
}

export interface HistoryJob {
  id: number
  main_scene: string
  sub_scene: string
  ratio: string
  image_prompt: string
  filename: string
  derived_from: string
  image_url: string
  copies: { seq: number; angle: string; headline: string; primary_text: string }[]
}

export interface ExportResult {
  xlsx: string
  zip: string
  images: number
  zip_url: string
}
