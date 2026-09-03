// 服务端状态 hooks：react-query 封装 + 后台任务轮询/收割。
import { App } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { api } from './client'
import type { RunBundle } from './types'

export const runKey = (run: string) => ['run', run] as const

export function useRuns() {
  return useQuery({ queryKey: ['runs'], queryFn: api.listRuns, refetchInterval: 15000 })
}

/** 当前任务的完整数据（state + status）。后台忙碌时 2.5s 轮询，空闲时不轮询。 */
export function useRunBundle(run: string | null) {
  return useQuery({
    queryKey: runKey(run ?? ''),
    queryFn: () => api.getRun(run!),
    enabled: !!run,
    refetchInterval: (query) => (query.state.data?.status.busy ? 2500 : false),
  })
}

/**
 * 后台任务收割：挖场景 / 生图文案管线 / 单图修改 完成后自动 ack + 刷新 + 提示。
 * 与 Streamlit 版页面顶部的收割区等价，但不锁页面。
 */
export function useHarvest(run: string | null, bundle: RunBundle | undefined) {
  const qc = useQueryClient()
  const { message } = App.useApp()
  const acking = useRef(new Set<string>())

  useEffect(() => {
    if (!run || !bundle) return
    const { status } = bundle
    const once = async (tag: string, fn: () => Promise<void>) => {
      if (acking.current.has(tag)) return
      acking.current.add(tag)
      try {
        await fn()
      } catch {
        /* ack 竞争（已被收割）无需处理 */
      } finally {
        acking.current.delete(tag)
        qc.invalidateQueries({ queryKey: runKey(run) })
      }
    }

    const m = status.mining
    if (m && m.state !== 'running') {
      void once(`mine`, async () => {
        await api.ackMining(run)
        if (m.state === 'finished') message.success(`场景挖掘完成，共 ${m.count} 个细分场景`)
        else message.error(`场景挖掘失败：${m.error}`)
      })
    }

    const p = status.pipeline
    if (p && p.state !== 'running' && !p.consumed) {
      void once(`pipe`, async () => {
        await api.ackPipeline(run)
        const label = p.kind === 'images' ? '生图' : '文案生成'
        if (p.state === 'failed') message.error(`${label}异常终止，已完成部分已保存`)
        else if (p.errors.length) message.warning(`${label}完成，${p.errors.length} 项失败可重试`)
        else message.success(`${label}完成`)
      })
    }

    for (const [i, es] of Object.entries(status.edits)) {
      if (es.state === 'running') continue
      void once(`edit-${i}`, async () => {
        await api.ackImageEdit(run, Number(i))
        if (es.state === 'finished') message.success('图片修改完成')
        else message.error(`图片修改失败:${es.error}`)
      })
    }
  }, [run, bundle, qc, message])
}
