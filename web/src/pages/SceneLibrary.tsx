// 场景库：跨任务的场景资产。筛选 + 勾选 → 一键创建生图任务（自动继承同产品参考图/品牌名）。
import { DeleteOutlined, PictureOutlined, RocketOutlined } from '@ant-design/icons'
import {
  App, Button, Card, Checkbox, Empty, Flex, Input, Popconfirm, Popover, Segmented,
  Select, Slider, Switch, Tag, Typography,
} from 'antd'
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import type { SceneLibRow } from '../api/types'
import { useCurrentTask } from '../store/task'

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

export default function SceneLibraryPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const nav = useNavigate()
  const { setCurrentRun } = useCurrentTask()

  const [keyword, setKeyword] = useState('')
  const [product, setProduct] = useState('')
  const [mains, setMains] = useState<string[]>([])
  const [useScore, setUseScore] = useState(false)
  const [scoreRange, setScoreRange] = useState<[number, number]>([90, 100])
  const [imgFilter, setImgFilter] = useState('全部')
  const [adsFilter, setAdsFilter] = useState('全部')
  const [order, setOrder] = useState<'score' | 'time'>('score')
  const [picked, setPicked] = useState<Set<number>>(new Set())

  const { data: products } = useQuery({ queryKey: ['sceneLibProducts'], queryFn: api.sceneLibProducts })
  const { data: mainOptions } = useQuery({
    queryKey: ['sceneLibMains', product],
    queryFn: () => api.sceneLibMainScenes(product),
  })

  const params = useMemo(() => {
    const p = new URLSearchParams()
    if (keyword.trim()) p.set('keyword', keyword.trim())
    if (product) p.set('product', product)
    mains.forEach((m) => p.append('main_scene', m))
    if (useScore) {
      p.set('score_min', String(scoreRange[0]))
      p.set('score_max', String(scoreRange[1]))
    }
    if (imgFilter !== '全部') p.set('has_image', imgFilter === '已出图' ? 'true' : 'false')
    if (adsFilter !== '全部') p.set('in_ads', adsFilter === '投放中' ? 'true' : 'false')
    p.set('order', order)
    return p
  }, [keyword, product, mains, useScore, scoreRange, imgFilter, adsFilter, order])

  const { data: rows, refetch } = useQuery({
    queryKey: ['sceneLib', params.toString()],
    queryFn: () => api.sceneLib(params),
  })

  const groups = useMemo(() => {
    const g = new Map<string, SceneLibRow[]>()
    for (const r of rows ?? []) g.set(r.main_scene, [...(g.get(r.main_scene) ?? []), r])
    return g
  }, [rows])

  const pickedRows = (rows ?? []).filter((r) => picked.has(r.id))

  const createTask = async () => {
    try {
      const res = await api.createTaskFromScenes(pickedRows.map((r) => r.id))
      message.success('已创建生图任务（自动继承同产品参考图与品牌名），正在进入工作台')
      setCurrentRun(res.name)
      setPicked(new Set())
      nav('/')
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  const deletePicked = async () => {
    await api.deleteScenes(pickedRows.map((r) => r.id))
    message.success(`已删除 ${pickedRows.length} 个场景（只删库记录，不影响历史任务文件）`)
    setPicked(new Set())
    void refetch()
    void qc.invalidateQueries({ queryKey: ['sceneLibProducts'] })
  }

  return (
    <div>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Flex gap={12} wrap align="center">
          <Input
            placeholder="关键词：场景名 / 描述…"
            allowClear
            style={{ width: 220 }}
            value={keyword}
            onChange={(ev) => setKeyword(ev.target.value)}
          />
          <Select
            placeholder="产品（全部）"
            allowClear
            style={{ minWidth: 220, maxWidth: 340 }}
            options={(products ?? []).map((p) => ({ value: p, label: p.slice(0, 40) + (p.length > 40 ? '…' : '') }))}
            value={product || undefined}
            onChange={(v) => {
              setProduct(v ?? '')
              setMains([])
            }}
          />
          <Select
            mode="multiple"
            allowClear
            placeholder="主场景分类"
            style={{ minWidth: 200 }}
            maxTagCount="responsive"
            options={(mainOptions ?? []).map((m) => ({ value: m, label: m }))}
            value={mains}
            onChange={setMains}
          />
          <Flex align="center" gap={8}>
            <Switch size="small" checked={useScore} onChange={setUseScore} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>按总分</Typography.Text>
            <Slider
              range min={0} max={100}
              value={scoreRange}
              onChange={(v) => setScoreRange(v as [number, number])}
              disabled={!useScore}
              style={{ width: 140 }}
            />
          </Flex>
          <Segmented options={['全部', '已出图', '未出图']} value={imgFilter} onChange={(v) => setImgFilter(v as string)} />
          <Segmented options={['全部', '投放中', '未投放']} value={adsFilter} onChange={(v) => setAdsFilter(v as string)} />
          <Segmented
            options={[{ label: '按分数', value: 'score' }, { label: '按时间', value: 'time' }]}
            value={order}
            onChange={(v) => setOrder(v as 'score' | 'time')}
          />
        </Flex>
      </Card>

      {!rows?.length ? (
        <Empty description="没有符合条件的场景。场景在工作台挖掘成功后自动入库。" style={{ marginTop: 60 }} />
      ) : (
        <>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>共 {rows.length} 个场景</Typography.Text>
          {[...groups.entries()].map(([main, items]) => (
            <div key={main} style={{ margin: '14px 0' }}>
              <Typography.Title level={5} style={{ marginBottom: 8 }}>{main}</Typography.Title>
              <Card size="small" styles={{ body: { padding: 0 } }}>
                {items.map((r, idx) => (
                  <Flex
                    key={r.id}
                    align="center"
                    gap={12}
                    style={{ padding: '10px 16px', borderTop: idx ? '1px solid #f2f3f7' : undefined }}
                  >
                    <Checkbox
                      checked={picked.has(r.id)}
                      onChange={(ev) => {
                        const next = new Set(picked)
                        if (ev.target.checked) next.add(r.id)
                        else next.delete(r.id)
                        setPicked(next)
                      }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Typography.Text strong>{r.sub_scene}</Typography.Text>
                      <Typography.Paragraph type="secondary" ellipsis={{ rows: 1 }} style={{ fontSize: 12, margin: 0 }}>
                        {r.description || '—'}
                      </Typography.Paragraph>
                    </div>
                    {r.total_score != null ? (
                      <Tag color={scoreColor(r.total_score)}>⭐ {r.total_score}</Tag>
                    ) : (
                      <Tag>无分</Tag>
                    )}
                    <Tag color={r.has_image ? 'cyan' : 'default'} icon={<PictureOutlined />}>
                      {r.has_image ? '已出图' : '未出图'}
                    </Tag>
                    <Flex align="center" gap={4}>
                      <Switch
                        size="small"
                        checked={!!r.in_ads}
                        onChange={async (v) => {
                          await api.setInAds(r.id, v)
                          void refetch()
                        }}
                      />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>投放中</Typography.Text>
                    </Flex>
                    <Popover
                      title={r.sub_scene}
                      content={
                        <div style={{ maxWidth: 360 }}>
                          {DETAIL_FIELDS.map(([k, label]) =>
                            r.detail?.[k] ? (
                              <Typography.Paragraph key={k} style={{ marginBottom: 6, fontSize: 13 }}>
                                <Typography.Text strong>{label}</Typography.Text>：{String(r.detail[k])}
                              </Typography.Paragraph>
                            ) : null,
                          )}
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            来源任务：{r.source_run || '—'}　入库：{r.created_at || '—'}
                          </Typography.Text>
                          {r.product_info && (
                            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
                              产品：{r.product_info.slice(0, 120)}{r.product_info.length > 120 ? '…' : ''}
                            </Typography.Paragraph>
                          )}
                        </div>
                      }
                    >
                      <Button size="small" type="link">详情</Button>
                    </Popover>
                  </Flex>
                ))}
              </Card>
            </div>
          ))}

          <div style={{ position: 'sticky', bottom: 16, zIndex: 5, marginTop: 16 }}>
            <Card size="small" style={{ boxShadow: '0 4px 24px rgba(0,0,0,0.10)' }}>
              <Flex align="center" gap={12}>
                <Typography.Text>已选 <b>{picked.size}</b> 个场景</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  创建生图任务 = 新建独立任务并进入工作台（场景已勾好，直接生成），不影响运行中的任务
                </Typography.Text>
                <div style={{ flex: 1 }} />
                <Button type="primary" icon={<RocketOutlined />} disabled={!picked.size} onClick={createTask}>
                  用选中场景创建生图任务
                </Button>
                <Popconfirm title={`删除选中的 ${picked.size} 个场景？只删库记录`} onConfirm={deletePicked}>
                  <Button danger icon={<DeleteOutlined />} disabled={!picked.size}>删除</Button>
                </Popconfirm>
              </Flex>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
