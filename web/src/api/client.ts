// 轻量请求封装：同源调用 FastAPI（开发时由 Vite 代理到 8000）。
// 错误统一抛 ApiError，message 取后端的 detail 字段。
import type {
  AssetItem, ExportResult, HistoryJob, HistoryRun, Product, PromptItem, PromptsPayload,
  RunBundle, RunListItem, RunState, RunStatus, SceneLibRow, SettingsPayload,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const data = await res.json()
      if (typeof data?.detail === 'string') detail = data.detail
      else if (data?.detail) detail = JSON.stringify(data.detail)
    } catch {
      /* 非 JSON 错误体，保留状态码信息 */
    }
    throw new ApiError(detail, res.status)
  }
  return res.json() as Promise<T>
}

const jbody = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
})

const e = encodeURIComponent

export const api = {
  // ---------------- 产品
  listProducts: () => req<Product[]>('/api/products'),
  createProduct: (body: { name: string; info?: string; brand_name?: string; ad_language?: string }) =>
    req<Product>('/api/products', jbody(body)),
  patchProduct: (id: number, body: Partial<Omit<Product, 'id' | 'run_count'>>) =>
    req<Product>(`/api/products/${id}`, { ...jbody(body), method: 'PATCH' }),

  // ---------------- 任务
  listRuns: () => req<RunListItem[]>('/api/runs'),
  createRun: (body: { product_id: number; ratio_choice?: string; title_count?: number }) =>
    req<{ name: string }>('/api/runs', jbody(body)),
  getRun: (run: string) => req<RunBundle>(`/api/runs/${e(run)}`),
  patchRun: (run: string, body: Record<string, unknown>) =>
    req<{ name: string; state: RunState }>(`/api/runs/${e(run)}`, { ...jbody(body), method: 'PATCH' }),
  runStatus: (run: string) => req<RunStatus>(`/api/runs/${e(run)}/status`),
  ackPipeline: (run: string) => req<{ ok: boolean }>(`/api/runs/${e(run)}/pipeline/ack`, { method: 'POST' }),
  exportRun: (run: string) => req<ExportResult>(`/api/runs/${e(run)}/export`, { method: 'POST' }),
  exportZipUrl: (run: string) => `/api/runs/${e(run)}/export/zip`,

  // ---------------- 工作流
  mineScenes: (run: string) => req<{ ok: boolean }>(`/api/runs/${e(run)}/scenes/mine`, { method: 'POST' }),
  ackMining: (run: string) => req<{ ok: boolean }>(`/api/runs/${e(run)}/scenes/mine/ack`, { method: 'POST' }),
  refineScenes: (run: string, feedback: string) =>
    req<{ state: RunState }>(`/api/runs/${e(run)}/scenes/refine`, jbody({ feedback })),
  generateImages: (run: string) => req<{ submitted: number }>(`/api/runs/${e(run)}/images/generate`, { method: 'POST' }),
  retryImages: (run: string) => req<{ submitted: number }>(`/api/runs/${e(run)}/images/retry`, { method: 'POST' }),
  regenerateImage: (run: string, i: number) =>
    req<{ ok: boolean }>(`/api/runs/${e(run)}/images/${i}/regenerate`, { method: 'POST' }),
  patchJob: (run: string, i: number, body: Record<string, unknown>) =>
    req<{ job: unknown }>(`/api/runs/${e(run)}/jobs/${i}`, { ...jbody(body), method: 'PATCH' }),
  refinePrompt: (run: string, i: number, feedback: string) =>
    req<{ job: unknown }>(`/api/runs/${e(run)}/jobs/${i}/prompt/refine`, jbody({ feedback })),
  editImage: (run: string, i: number, feedback: string) =>
    req<{ ok: boolean }>(`/api/runs/${e(run)}/jobs/${i}/image/edit`, jbody({ feedback })),
  ackImageEdit: (run: string, i: number) =>
    req<{ result: unknown }>(`/api/runs/${e(run)}/jobs/${i}/image/ack`, { method: 'POST' }),
  gotoVersion: (run: string, i: number, version: number) =>
    req<{ state: RunState }>(`/api/runs/${e(run)}/jobs/${i}/image/goto`, jbody({ version })),
  generateCopies: (run: string, titleCount?: number) =>
    req<{ submitted: number }>(`/api/runs/${e(run)}/copies/generate`, jbody({ title_count: titleCount ?? null })),
  refineCopies: (run: string, i: number, feedback: string) =>
    req<{ job: unknown }>(`/api/runs/${e(run)}/jobs/${i}/copies/refine`, jbody({ feedback })),

  // ---------------- 参考图
  uploadRefs: (run: string, kind: 'style' | 'logo', files: File[]) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    return req<Record<string, string[]>>(`/api/runs/${e(run)}/refs/${kind}`, { method: 'POST', body: fd })
  },
  deleteRef: (run: string, kind: 'style' | 'logo', rel: string) =>
    req<Record<string, string[]>>(`/api/runs/${e(run)}/refs/${kind}?rel=${e(rel)}`, { method: 'DELETE' }),
  applyAssets: (run: string, kind: 'style' | 'logo', paths: string[]) =>
    req<Record<string, string[]>>(`/api/runs/${e(run)}/refs/${kind}/apply`, jbody({ paths })),
  listAssets: (kind: 'style' | 'logo') => req<AssetItem[]>(`/api/assets/${kind}`),
  deleteAsset: (kind: 'style' | 'logo', path: string) =>
    req<{ ok: boolean }>(`/api/assets/${kind}?path=${e(path)}`, { method: 'DELETE' }),

  // ---------------- 提示词 / 设置
  getPrompts: () => req<PromptsPayload>('/api/prompts'),
  savePrompt: (key: string, template: string) =>
    req<{ key: string; prompt: PromptItem }>(`/api/prompts/${e(key)}`, { ...jbody({ template }), method: 'PUT' }),
  resetPrompt: (key: string) => req<{ key: string; prompt: PromptItem }>(`/api/prompts/${e(key)}/reset`, { method: 'POST' }),
  getSettings: () => req<SettingsPayload>('/api/settings'),
  saveSettings: (config: SettingsPayload['config']) =>
    req<SettingsPayload>('/api/settings', { ...jbody(config), method: 'PUT' }),
  listModels: () => req<{ llm: string[]; image: string[]; errors: Record<string, string> }>('/api/settings/models'),
  testConnection: () =>
    req<Record<'claude' | 'image', { ok: boolean; message: string }>>('/api/settings/test', { method: 'POST' }),

  // ---------------- 历史 / 场景库
  historyRuns: (keyword: string) => req<HistoryRun[]>(`/api/history/runs?keyword=${e(keyword)}`),
  historyJobs: (runId: number) => req<{ run: HistoryRun; jobs: HistoryJob[] }>(`/api/history/runs/${runId}/jobs`),
  sceneLib: (params: URLSearchParams) => req<SceneLibRow[]>(`/api/scene-lib?${params}`),
  sceneLibProducts: () => req<Pick<Product, 'id' | 'name' | 'info'>[]>('/api/scene-lib/products'),
  sceneLibMainScenes: (productId: number | null) =>
    req<string[]>(`/api/scene-lib/main-scenes${productId != null ? `?product_id=${productId}` : ''}`),
  setInAds: (id: number, inAds: boolean) =>
    req<{ ok: boolean }>(`/api/scene-lib/${id}`, { ...jbody({ in_ads: inAds }), method: 'PATCH' }),
  deleteScenes: (ids: number[]) => req<{ deleted: number }>('/api/scene-lib/delete', jbody({ ids })),
  createTaskFromScenes: (ids: number[]) => req<{ name: string }>('/api/scene-lib/create-task', jbody({ ids })),
}
