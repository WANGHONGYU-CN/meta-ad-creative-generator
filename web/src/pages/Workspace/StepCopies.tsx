// 第四步：看图写文案 + 导出交付包。每张图的文案可直接编辑/增删/AI 修改。
import { CommentOutlined, DeleteOutlined, DownloadOutlined, FileZipOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { App, Button, Card, Divider, Empty, Flex, Image, Input, InputNumber, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import { runKey } from '../../api/hooks'
import type { Copy, ExportResult, Job, RunBundle } from '../../api/types'
import ChatDrawer from '../../components/ChatDrawer'

function CopiesEditor({ run, index, job }: { run: string; index: number; job: Job }) {
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [rows, setRows] = useState<Copy[]>(job.copies)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setRows(job.copies)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.rev, job.copies.length])

  const dirty = JSON.stringify(rows) !== JSON.stringify(job.copies)
  const upd = (i: number, patch: Partial<Copy>) =>
    setRows((r) => r.map((row, k) => (k === i ? { ...row, ...patch } : row)))

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      {rows.map((c, i) => (
        <Flex key={i} gap={8} style={{ marginBottom: 8 }} align="start">
          <Input
            style={{ width: 130 }}
            value={c.angle}
            onChange={(ev) => upd(i, { angle: ev.target.value })}
            placeholder="角度"
          />
          <Input
            style={{ width: 260 }}
            value={c.headline}
            onChange={(ev) => upd(i, { headline: ev.target.value })}
            placeholder="标题 Headline"
          />
          <Input.TextArea
            autoSize={{ minRows: 1, maxRows: 3 }}
            style={{ flex: 1 }}
            value={c.primary_text}
            onChange={(ev) => upd(i, { primary_text: ev.target.value })}
            placeholder="主文案 Primary Text"
          />
          <Button type="text" danger icon={<DeleteOutlined />} onClick={() => setRows((r) => r.filter((_, k) => k !== i))} />
        </Flex>
      ))}
      <Flex gap={8}>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setRows((r) => [...r, { angle: '', headline: '', primary_text: '' }])}>
          加一套
        </Button>
        {dirty && (
          <Button
            size="small" type="primary" loading={saving}
            onClick={async () => {
              setSaving(true)
              try {
                await api.patchJob(run, index, { copies: rows })
                message.success('文案已保存')
                await qc.invalidateQueries({ queryKey: runKey(run) })
              } catch (err) {
                message.error(err instanceof Error ? err.message : String(err))
              } finally {
                setSaving(false)
              }
            }}
          >
            保存文案
          </Button>
        )}
      </Flex>
    </div>
  )
}

export default function StepCopies({ run, bundle }: { run: string; bundle: RunBundle }) {
  const s = bundle.state
  const g = s.jobs_gen
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [titleCount, setTitleCount] = useState(s.title_count)
  const [chatIdx, setChatIdx] = useState<number | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exported, setExported] = useState<ExportResult | null>(null)

  const withImage = s.jobs.map((j, i) => [j, i] as const).filter(([j]) => j.image_url)
  const needCopy = withImage.filter(([j]) => !j.copies.length)
  const running = bundle.status.pipeline?.state === 'running'

  const generate = async () => {
    try {
      const res = await api.generateCopies(run, titleCount)
      message.success(`已提交 ${res.submitted} 张图的文案后台生成`)
      await qc.invalidateQueries({ queryKey: runKey(run) })
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  const doExport = async () => {
    setExporting(true)
    try {
      const res = await api.exportRun(run)
      setExported(res)
      message.success(`已导出 ${res.images} 张素材`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    } finally {
      setExporting(false)
    }
  }

  if (!withImage.length) {
    return <Empty description="还没有已生成的图片，先在上一步出图" style={{ marginTop: 60 }} />
  }

  return (
    <div>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Flex align="center" gap={12} wrap>
          <Space size={6}>
            <Typography.Text type="secondary">每图</Typography.Text>
            <InputNumber min={1} max={10} value={titleCount} onChange={(v) => setTitleCount(v ?? 3)} />
            <Typography.Text type="secondary">套文案</Typography.Text>
          </Space>
          <Button
            type="primary" icon={<ThunderboltOutlined />}
            disabled={!needCopy.length || bundle.status.busy}
            onClick={generate}
          >
            为 {needCopy.length} 张图生成文案
          </Button>
          {running && bundle.status.pipeline?.kind === 'copies' && (
            <Tag color="processing">生成中 {bundle.status.pipeline.done}/{bundle.status.pipeline.total}</Tag>
          )}
          <div style={{ flex: 1 }} />
          <Button icon={<FileZipOutlined />} loading={exporting} onClick={doExport} disabled={bundle.status.busy}>
            导出交付包
          </Button>
          {exported && (
            <Button type="primary" icon={<DownloadOutlined />} href={api.exportZipUrl(run)}>
              下载 交付包.zip（{exported.images} 张图 + 交付表）
            </Button>
          )}
        </Flex>
      </Card>

      {withImage.map(([job, i]) => (
        <Card key={`${g}-${i}`} size="small" style={{ marginBottom: 12 }}>
          <Flex gap={16} align="start">
            <div style={{ width: 170, flexShrink: 0 }}>
              <Image src={job.image_url} width={170} style={{ borderRadius: 8 }} />
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '6px 0 0' }}>
                {job.main_scene} / {job.sub_scene}（{job.ratio}）
              </Typography.Paragraph>
            </div>
            {job.copies.length ? (
              <CopiesEditor run={run} index={i} job={job} />
            ) : (
              <Flex flex={1} align="center" justify="center" style={{ padding: 24 }}>
                <Typography.Text type="secondary">
                  {running ? '文案生成中…' : '尚无文案，点上方按钮批量生成'}
                </Typography.Text>
              </Flex>
            )}
            {job.copies.length > 0 && (
              <Button icon={<CommentOutlined />} onClick={() => setChatIdx(i)}>
                AI 修改
              </Button>
            )}
          </Flex>
        </Card>
      ))}

      <Divider />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        导出内容：图片 + manifest.json + 交付表.xlsx（每套文案一行，与图片文件名绑定），
        文件同时保留在任务目录 outputs/{run}/。
      </Typography.Text>

      {chatIdx != null && s.jobs[chatIdx] && (
        <ChatDrawer
          open
          onClose={() => setChatIdx(null)}
          title={`AI 修改文案 · ${s.jobs[chatIdx].sub_scene}（${s.jobs[chatIdx].ratio}）`}
          subtitle="AI 会看着这张图和你的意见整批修改该图的文案。"
          history={s.chats[`chat_copies_${g}_${chatIdx}`] ?? []}
          placeholder="例：语气更年轻；第 2 套换成促销角度；标题都加 emoji…"
          onSend={async (fb) => {
            await api.refineCopies(run, chatIdx, fb)
            await qc.invalidateQueries({ queryKey: runKey(run) })
          }}
        />
      )}
    </div>
  )
}
