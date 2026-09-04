// 第一步：产品信息 + 参考图（风格图/Logo，含全局图库选用）+ 挖掘场景。
import { DeleteOutlined, FolderOpenOutlined, InboxOutlined, SearchOutlined } from '@ant-design/icons'
import {
  App, Button, Card, Checkbox, Col, Flex, Image, Input, InputNumber, Modal,
  Popconfirm, Row, Segmented, Space, Typography, Upload,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import { runKey } from '../../api/hooks'
import type { RunBundle } from '../../api/types'

const RATIO_OPTIONS = [
  { label: '1:1 方图', value: '1:1' },
  { label: '4:5 竖图', value: '4:5' },
  { label: '双尺寸（4:5 母版 + 1:1）', value: 'dual' },
]

function ratioAlias(label: string): string {
  if (label.includes('双尺寸')) return 'dual'
  if (label.includes('4:5')) return '4:5'
  return '1:1'
}

// ---------------------------------------------------------------- 参考图区块
function RefSection({ run, kind, title, hint, rels, urls }: {
  run: string
  kind: 'style' | 'logo'
  title: string
  hint: string
  rels: string[]
  urls: string[]
}) {
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [libOpen, setLibOpen] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const multi = kind === 'style'
  const { data: assets, refetch } = useQuery({
    queryKey: ['assets', kind],
    queryFn: () => api.listAssets(kind),
    enabled: libOpen,
  })

  const refresh = () => qc.invalidateQueries({ queryKey: runKey(run) })

  const doUpload = async (files: File[]) => {
    try {
      await api.uploadRefs(run, kind, multi ? files : files.slice(0, 1))
      message.success(`已上传 ${multi ? files.length : 1} 张（整组替换）`)
      refresh()
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  const applyPicked = async () => {
    try {
      await api.applyAssets(run, kind, picked)
      message.success('已应用所选参考图（整组替换）')
      setLibOpen(false)
      setPicked([])
      refresh()
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Card size="small" title={title} extra={
      <Button size="small" type="text" icon={<FolderOpenOutlined />} onClick={() => setLibOpen(true)}>
        从历史图选择
      </Button>
    }>
      <Upload.Dragger
        multiple={multi}
        maxCount={multi ? 3 : 1}
        accept=".png,.jpg,.jpeg,.webp"
        showUploadList={false}
        beforeUpload={(file, list) => {
          if (file === list[0]) void doUpload(list as unknown as File[])
          return Upload.LIST_IGNORE
        }}
      >
        <p style={{ margin: 0 }}><InboxOutlined style={{ color: '#4f46e5', fontSize: 22 }} /></p>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{hint}</Typography.Text>
      </Upload.Dragger>
      {urls.length > 0 && (
        <Flex gap={8} wrap style={{ marginTop: 12 }}>
          {urls.map((u, i) => (
            <div key={u} style={{ position: 'relative' }}>
              <Image src={u} width={88} height={88} style={{ objectFit: 'cover', borderRadius: 8 }} />
              <Popconfirm title="删除这张参考图？" onConfirm={async () => {
                await api.deleteRef(run, kind, rels[i])
                refresh()
              }}>
                <Button
                  size="small" danger type="text" icon={<DeleteOutlined />}
                  style={{ position: 'absolute', top: 0, right: 0, background: '#ffffffcc' }}
                />
              </Popconfirm>
            </div>
          ))}
        </Flex>
      )}
      <Modal
        open={libOpen}
        onCancel={() => setLibOpen(false)}
        title={`历史${title}（跨任务累积，按内容去重）`}
        width={640}
        okText={`应用所选 ${picked.length} 张（整组替换）`}
        okButtonProps={{ disabled: !picked.length || (!multi && picked.length > 1) }}
        onOk={applyPicked}
      >
        {!multi && picked.length > 1 && <Typography.Text type="danger">Logo 只能选 1 张</Typography.Text>}
        <Flex gap={12} wrap style={{ marginTop: 8, maxHeight: 420, overflowY: 'auto' }}>
          {(assets ?? []).map((a) => (
            <div key={a.path} style={{ width: 120, textAlign: 'center' }}>
              <div style={{ position: 'relative' }}>
                <Image src={a.url} width={120} height={120} style={{ objectFit: 'cover', borderRadius: 8 }} preview={false} />
                <Checkbox
                  checked={picked.includes(a.path)}
                  onChange={(ev) =>
                    setPicked((p) => (ev.target.checked ? [...p, a.path] : p.filter((x) => x !== a.path)))
                  }
                  style={{ position: 'absolute', top: 4, left: 6, background: '#ffffffcc', borderRadius: 4, padding: '0 4px' }}
                />
                <Popconfirm title="从图库删除？不影响已生成的任务" onConfirm={async () => {
                  await api.deleteAsset(kind, a.path)
                  void refetch()
                }}>
                  <Button size="small" danger type="text" icon={<DeleteOutlined />}
                    style={{ position: 'absolute', top: 2, right: 2, background: '#ffffffcc' }} />
                </Popconfirm>
              </div>
              <Typography.Text ellipsis style={{ fontSize: 12 }} title={a.name}>{a.name}</Typography.Text>
            </div>
          ))}
          {(assets ?? []).length === 0 && (
            <Typography.Text type="secondary">暂无历史图，上传过一次后这里会自动累积。</Typography.Text>
          )}
        </Flex>
      </Modal>
    </Card>
  )
}

// ---------------------------------------------------------------- 主面板
export default function StepInput({ run, bundle, onMined }: {
  run: string
  bundle: RunBundle
  onMined: () => void
}) {
  const s = bundle.state
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [form, setForm] = useState({
    product_info: s.product_info,
    brand_name: s.brand_name,
    ad_language: s.ad_language,
    ratio: ratioAlias(s.ratio_choice),
    title_count: s.title_count,
  })
  const [saving, setSaving] = useState(false)

  // 切任务时重置表单（轮询刷新不覆盖正在编辑的内容）
  useEffect(() => {
    setForm({
      product_info: s.product_info,
      brand_name: s.brand_name,
      ad_language: s.ad_language,
      ratio: ratioAlias(s.ratio_choice),
      title_count: s.title_count,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  const dirty = useMemo(
    () =>
      form.product_info !== s.product_info ||
      form.brand_name !== s.brand_name ||
      form.ad_language !== s.ad_language ||
      form.ratio !== ratioAlias(s.ratio_choice) ||
      form.title_count !== s.title_count,
    [form, s],
  )

  const save = async () => {
    setSaving(true)
    try {
      await api.patchRun(run, {
        product_info: form.product_info,
        brand_name: form.brand_name,
        ad_language: form.ad_language,
        ratio_choice: form.ratio,
        title_count: form.title_count,
      })
      await qc.invalidateQueries({ queryKey: runKey(run) })
    } finally {
      setSaving(false)
    }
  }

  const mining = bundle.status.mining?.state === 'running'

  const mine = async () => {
    try {
      if (dirty) await save()
      await api.mineScenes(run)
      message.info('已提交挖掘，完成后自动进入「投放场景」')
      await qc.invalidateQueries({ queryKey: runKey(run) })
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Row gutter={20}>
      <Col span={14}>
        <Card title={`产品信息 · ${s.product_name || '未命名产品'}`}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <Input.TextArea
              rows={5}
              value={form.product_info}
              onChange={(ev) => setForm({ ...form, product_info: ev.target.value })}
              placeholder="产品名称、卖点、目标人群、目标市场等，越具体场景越准。例：便携颈挂风扇，超静音/续航18小时/可折叠，目标人群欧美户外通勤，市场美国…"
            />
            <Flex gap={12}>
              <Input
                addonBefore="品牌名"
                value={form.brand_name}
                onChange={(ev) => setForm({ ...form, brand_name: ev.target.value })}
                placeholder="融入海报设计，可留空"
              />
              <Input
                addonBefore="广告语言"
                value={form.ad_language}
                onChange={(ev) => setForm({ ...form, ad_language: ev.target.value })}
                placeholder="English / 中文 / Español…"
              />
            </Flex>
            <Flex gap={16} align="center" wrap>
              <Segmented
                options={RATIO_OPTIONS}
                value={form.ratio}
                onChange={(v) => setForm({ ...form, ratio: v as string })}
              />
              <Space size={6}>
                <Typography.Text type="secondary">每图文案</Typography.Text>
                <InputNumber
                  min={1} max={10}
                  value={form.title_count}
                  onChange={(v) => setForm({ ...form, title_count: v ?? 3 })}
                />
                <Typography.Text type="secondary">套</Typography.Text>
              </Space>
            </Flex>
            <Flex gap={12}>
              {dirty && <Button onClick={save} loading={saving}>保存修改</Button>}
              {s.scenes.length > 0 ? (
                <Popconfirm title="重新挖掘会替换当前场景列表（已生成的图片不受影响）" onConfirm={mine}>
                  <Button type="primary" icon={<SearchOutlined />} disabled={!form.product_info.trim() || mining} loading={mining}>
                    重新挖掘场景
                  </Button>
                </Popconfirm>
              ) : (
                <Button type="primary" icon={<SearchOutlined />} onClick={mine}
                  disabled={!form.product_info.trim() || mining} loading={mining}>
                  AI 挖掘投放场景
                </Button>
              )}
              {s.scenes.length > 0 && !mining && (
                <Button onClick={onMined}>已有 {s.scenes.length} 个场景，去选择 →</Button>
              )}
            </Flex>
          </Space>
        </Card>
      </Col>
      <Col span={10}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <RefSection
            run={run} kind="style" title="海报风格参考图"
            hint="可选 1-3 张，海报会贴近其风格/配色/排版；重新上传即整组替换"
            rels={s.style_images} urls={s.style_images_urls}
          />
          <RefSection
            run={run} kind="logo" title="品牌 Logo"
            hint="可选 1 张，建议透明底 PNG，会放进海报角落"
            rels={s.logo_images} urls={s.logo_images_urls}
          />
          {s.style_images.length === 0 && s.logo_images.length === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              ⚠ 未带任何参考图时将纯文生图；可上传或从历史图库选择。
            </Typography.Text>
          )}
        </Space>
      </Col>
    </Row>
  )
}
