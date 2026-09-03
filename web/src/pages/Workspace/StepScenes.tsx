// 第二步：场景选择。评分徽章卡片 + 筛选工具条 + 底部悬浮操作栏。
// 精简模式默认开：每个主场景组只显示评分前 30%（至少 2 个，已选恒显示）。
import { CheckCircleFilled, CommentOutlined, PictureOutlined } from '@ant-design/icons'
import { App, Badge, Button, Card, Col, Flex, Input, Popconfirm, Popover, Row, Segmented, Select, Slider, Switch, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import { runKey } from '../../api/hooks'
import type { RunBundle, SceneRow } from '../../api/types'
import ChatDrawer from '../../components/ChatDrawer'

const DETAIL_FIELDS: [string, string][] = [
  ['audience', '目标用户'],
  ['trigger', '触发时刻'],
  ['pain_or_desire', '痛点/渴望'],
  ['product_use', '产品使用链路'],
]

function scoreColor(v: number) {
  if (v >= 90) return 'green'
  if (v >= 80) return 'blue'
  if (v >= 70) return 'orange'
  return 'default'
}

function SceneDetail({ row }: { row: SceneRow }) {
  const d = row.detail ?? {}
  const sb = d.score_breakdown ?? {}
  return (
    <div style={{ maxWidth: 360 }}>
      {DETAIL_FIELDS.map(([k, label]) =>
        d[k] ? (
          <Typography.Paragraph key={k} style={{ marginBottom: 6, fontSize: 13 }}>
            <Typography.Text strong>{label}</Typography.Text>：{String(d[k])}
          </Typography.Paragraph>
        ) : null,
      )}
      {Object.keys(sb).length > 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          产品匹配 {sb.product_fit ?? '-'} / 画面直观 {sb.visual_clarity ?? '-'} / 付费意愿 {sb.purchase_intent ?? '-'} /
          情绪吸引 {sb.attention_emotion ?? '-'} / 投放安全 {sb.meta_safety ?? '-'}
        </Typography.Text>
      )}
    </div>
  )
}

export default function StepScenes({ run, bundle, onGenerate }: {
  run: string
  bundle: RunBundle
  onGenerate: () => void
}) {
  const s = bundle.state
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [selected, setSelected] = useState<Set<number>>(new Set(s.selected_scenes))
  const [mains, setMains] = useState<string[]>([])
  const [minScore, setMinScore] = useState(0)
  const [compact, setCompact] = useState(true)
  const [keyword, setKeyword] = useState('')
  const [chatOpen, setChatOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setSelected(new Set(s.selected_scenes))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, s.scenes.length])

  const mainNames = useMemo(() => [...new Set(s.scenes.map((r) => r.main_scene))], [s.scenes])
  const hasScores = useMemo(() => s.scenes.some((r) => r.detail?.total_score != null), [s.scenes])
  const scoreOf = (idx: number) => {
    const v = s.scenes[idx]?.detail?.total_score
    return typeof v === 'number' ? v : -1
  }

  // 筛选（只影响展示，不影响勾选）
  const visible = useMemo(() => {
    const kw = keyword.trim()
    return s.scenes
      .map((_, i) => i)
      .filter((i) => {
        const row = s.scenes[i]
        if (mains.length && !mains.includes(row.main_scene)) return false
        if (minScore > 0 && scoreOf(i) < minScore) return false
        if (kw && !`${row.main_scene}${row.sub_scene}${row.description}`.includes(kw)) return false
        return true
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.scenes, mains, minScore, keyword])

  // 分组 + 精简模式
  const groups = useMemo(() => {
    const g = new Map<string, number[]>()
    visible.forEach((i) => {
      const key = s.scenes[i].main_scene
      g.set(key, [...(g.get(key) ?? []), i])
    })
    const collapsed = new Map<string, number>()
    if (compact) {
      for (const [main, indices] of g) {
        const keep = Math.max(2, Math.ceil(indices.length * 0.3))
        const top = new Set([...indices].sort((a, b) => scoreOf(b) - scoreOf(a)).slice(0, keep))
        const shown = indices.filter((i) => top.has(i) || selected.has(i))
        collapsed.set(main, indices.length - shown.length)
        g.set(main, shown)
      }
    }
    return { g, collapsed }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, compact, selected, s.scenes])

  const toggle = (idx: number) => {
    const next = new Set(selected)
    if (next.has(idx)) next.delete(idx)
    else next.add(idx)
    setSelected(next)
    // 同步服务端（失败提示但不回滚本地——下次轮询会校正）
    api.patchRun(run, { selected_scenes: [...next] }).catch((err: Error) => message.error(err.message))
  }

  const ratioCount = s.ratio_choice.includes('双尺寸') ? 2 : 1
  const nSel = [...selected].filter((i) => i < s.scenes.length).length

  const generate = async () => {
    setSubmitting(true)
    try {
      const res = await api.generateImages(run)
      message.success(`已提交 ${res.submitted} 张图后台生成`)
      await qc.invalidateQueries({ queryKey: runKey(run) })
      onGenerate()
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  const hasJobs = s.jobs.length > 0

  return (
    <div>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Flex gap={16} align="center" wrap>
          <Select
            mode="multiple"
            allowClear
            placeholder="筛选主场景（不选 = 全部）"
            style={{ minWidth: 260 }}
            options={mainNames.map((m) => ({ value: m, label: m }))}
            value={mains}
            onChange={setMains}
            maxTagCount="responsive"
          />
          {hasScores && (
            <Flex align="center" gap={8} style={{ width: 220 }}>
              <Typography.Text type="secondary" style={{ whiteSpace: 'nowrap', fontSize: 12 }}>最低评分</Typography.Text>
              <Slider min={0} max={100} value={minScore} onChange={setMinScore} style={{ flex: 1 }} />
            </Flex>
          )}
          <Input
            prefix={<span style={{ color: '#bbb' }}>🔍</span>}
            placeholder="搜索场景"
            allowClear
            style={{ width: 180 }}
            value={keyword}
            onChange={(ev) => setKeyword(ev.target.value)}
          />
          <Flex align="center" gap={6}>
            <Switch checked={compact} onChange={setCompact} size="small" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>精简模式（每组只看高分）</Typography.Text>
          </Flex>
          <div style={{ flex: 1 }} />
          <Button icon={<CommentOutlined />} onClick={() => setChatOpen(true)}>AI 修改场景</Button>
        </Flex>
        {visible.length < s.scenes.length && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            筛选后显示 {visible.length} / {s.scenes.length} 个细分场景（勾选状态不受筛选影响）
          </Typography.Text>
        )}
      </Card>

      {[...groups.g.entries()].map(([main, indices]) => (
        <div key={main} style={{ marginBottom: 20 }}>
          <Flex align="center" gap={8} style={{ marginBottom: 10 }}>
            <Typography.Title level={5} style={{ margin: 0 }}>{main}</Typography.Title>
            {(groups.collapsed.get(main) ?? 0) > 0 && (
              <Tag>已收起 {groups.collapsed.get(main)} 个低分场景</Tag>
            )}
          </Flex>
          <Row gutter={[12, 12]}>
            {indices.map((idx) => {
              const row = s.scenes[idx]
              const picked = selected.has(idx)
              const score = row.detail?.total_score
              return (
                <Col key={idx} xs={24} sm={12} lg={8} xl={6}>
                  <Card
                    size="small"
                    hoverable
                    onClick={() => toggle(idx)}
                    style={{
                      height: '100%',
                      borderColor: picked ? '#4f46e5' : undefined,
                      boxShadow: picked ? '0 0 0 1px #4f46e5' : undefined,
                      cursor: 'pointer',
                    }}
                  >
                    <Flex align="start" gap={8}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <Flex align="center" gap={6} wrap>
                          {picked && <CheckCircleFilled style={{ color: '#4f46e5' }} />}
                          <Typography.Text strong ellipsis title={row.sub_scene}>{row.sub_scene}</Typography.Text>
                        </Flex>
                        <Typography.Paragraph
                          type="secondary"
                          ellipsis={{ rows: 2 }}
                          style={{ fontSize: 12, margin: '6px 0 0' }}
                        >
                          {row.description || '—'}
                        </Typography.Paragraph>
                      </div>
                      {typeof score === 'number' && <Tag color={scoreColor(score)}>{score}</Tag>}
                    </Flex>
                    {row.detail && (
                      <Popover content={<SceneDetail row={row} />} title={row.sub_scene}>
                        <Button size="small" type="link" style={{ padding: 0, fontSize: 12 }} onClick={(ev) => ev.stopPropagation()}>
                          详情
                        </Button>
                      </Popover>
                    )}
                  </Card>
                </Col>
              )
            })}
          </Row>
        </div>
      ))}

      {/* 底部悬浮操作栏 */}
      <div style={{ position: 'sticky', bottom: 16, zIndex: 5 }}>
        <Card size="small" style={{ boxShadow: '0 4px 24px rgba(0,0,0,0.10)' }}>
          <Flex align="center" gap={16}>
            <Badge count={nSel} color="#4f46e5" showZero>
              <Typography.Text style={{ paddingRight: 8 }}>已选场景</Typography.Text>
            </Badge>
            <Typography.Text type="secondary">
              × {ratioCount} 个尺寸 = 将生成 {nSel * ratioCount} 张图
              {ratioCount > 1 && '（每场景 4:5 母版 + 改尺寸 1:1）'}
            </Typography.Text>
            <div style={{ flex: 1 }} />
            <Segmented
              value={s.ratio_choice.includes('双尺寸') ? 'dual' : s.ratio_choice.includes('4:5') ? '4:5' : '1:1'}
              options={[
                { label: '1:1', value: '1:1' },
                { label: '4:5', value: '4:5' },
                { label: '双尺寸', value: 'dual' },
              ]}
              onChange={async (v) => {
                await api.patchRun(run, { ratio_choice: v as string })
                await qc.invalidateQueries({ queryKey: runKey(run) })
              }}
            />
            {hasJobs ? (
              <Popconfirm
                title="重新生成会替换当前这批图片任务"
                description="已生成的图片文件保留在磁盘，但工作台列表会按新勾选重建。"
                onConfirm={generate}
              >
                <Button type="primary" icon={<PictureOutlined />} disabled={!nSel || bundle.status.busy} loading={submitting}>
                  重新生成这批图
                </Button>
              </Popconfirm>
            ) : (
              <Button
                type="primary" icon={<PictureOutlined />}
                disabled={!nSel || bundle.status.busy} loading={submitting}
                onClick={generate}
              >
                生成 {nSel * ratioCount} 张图
              </Button>
            )}
          </Flex>
        </Card>
      </div>

      <ChatDrawer
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        title="AI 修改场景结果"
        subtitle="AI 会基于当前场景列表 + 你的历史意见进行修改；修改后勾选会被清空。"
        history={s.chats['chat_scenes'] ?? []}
        placeholder="例：场景太泛了，聚焦冬季户外；把第 2 个主场景换成送礼场景…"
        onSend={async (fb) => {
          await api.refineScenes(run, fb)
          await qc.invalidateQueries({ queryKey: runKey(run) })
        }}
      />
    </div>
  )
}
