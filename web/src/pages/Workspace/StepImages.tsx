// 第三步：生图画廊。卡片式图片墙，每张图支持 重新生成 / 对话修改（后台并发）/
// 提示词编辑 / 版本历史回跳；派生图（双尺寸 1:1）显示与母版的关系。
import {
  CommentOutlined, EditOutlined, FileTextOutlined, HistoryOutlined,
  LoadingOutlined, ReloadOutlined, RightOutlined,
} from '@ant-design/icons'
import {
  App, Button, Card, Col, Drawer, Flex, Image, Input, Modal, Popconfirm, Row,
  Skeleton, Space, Tag, Tooltip, Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import { runKey } from '../../api/hooks'
import type { Job, RunBundle } from '../../api/types'
import ChatDrawer, { ChatPanel } from '../../components/ChatDrawer'

function jobTitle(job: Job) {
  return `${job.main_scene} / ${job.sub_scene}`
}

// ---------------------------------------------------------------- 版本历史弹窗
function VersionModal({ run, index, job, open, onClose }: {
  run: string
  index: number
  job: Job
  open: boolean
  onClose: () => void
}) {
  const qc = useQueryClient()
  const { message } = App.useApp()
  const hist = job.hist_urls
  const cur = job.hist_idx ?? hist.length - 1

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={720} title={`版本历史 · ${jobTitle(job)}（${job.ratio}）`}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        最多保留最近 10 版；回到旧版后再修改，会丢弃它后面的版本。母版换版本后，1:1 派生图需要重新改尺寸。
      </Typography.Text>
      <Flex gap={12} wrap style={{ marginTop: 12 }}>
        {hist.map((u, k) => (
          <div key={u} style={{ width: 150, textAlign: 'center' }}>
            <Image
              src={u} width={150}
              style={{ borderRadius: 8, border: k === cur ? '2px solid #4f46e5' : '2px solid transparent' }}
            />
            <div style={{ marginTop: 4 }}>
              {k === cur ? (
                <Tag color="blue">当前 · 第 {k + 1} 版</Tag>
              ) : (
                <Button
                  size="small"
                  onClick={async () => {
                    try {
                      await api.gotoVersion(run, index, k)
                      message.success(`已回到第 ${k + 1} 版`)
                      await qc.invalidateQueries({ queryKey: runKey(run) })
                      onClose()
                    } catch (err) {
                      message.error(err instanceof Error ? err.message : String(err))
                    }
                  }}
                >
                  回到第 {k + 1} 版
                </Button>
              )}
            </div>
          </div>
        ))}
      </Flex>
    </Modal>
  )
}

// ---------------------------------------------------------------- 主面板
export default function StepImages({ run, bundle, onToCopies }: {
  run: string
  bundle: RunBundle
  onToCopies: () => void
}) {
  const s = bundle.state
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [chatIdx, setChatIdx] = useState<number | null>(null)
  const [promptIdx, setPromptIdx] = useState<number | null>(null)
  const [versionIdx, setVersionIdx] = useState<number | null>(null)
  const [promptText, setPromptText] = useState('')

  const pipelineRunning = bundle.status.pipeline?.state === 'running'
  const editingSet = new Set(
    Object.entries(bundle.status.edits)
      .filter(([, es]) => es.state === 'running')
      .map(([i]) => Number(i)),
  )

  const refresh = () => qc.invalidateQueries({ queryKey: runKey(run) })

  const masterOf = (job: Job) =>
    s.jobs.find((m) => !m.derived_from && m.filename === job.derived_from)

  const pendingCount = s.jobs.filter((j) => !j.image_url).length
  const doneCount = s.jobs.filter((j) => j.image_url).length

  const call = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn()
      if (ok) message.success(ok)
      refresh()
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    if (promptIdx != null) setPromptText(s.jobs[promptIdx]?.image_prompt ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptIdx])

  return (
    <div>
      <Flex align="center" gap={12} style={{ marginBottom: 16 }}>
        <Typography.Text type="secondary">
          共 {s.jobs.length} 张图，已完成 {doneCount} 张
        </Typography.Text>
        <div style={{ flex: 1 }} />
        {pendingCount > 0 && !pipelineRunning && (
          <Button icon={<ReloadOutlined />} onClick={() => void call(() => api.retryImages(run), '已提交补齐/重试')}>
            补齐/重试 {pendingCount} 张
          </Button>
        )}
        {doneCount > 0 && (
          <Button type="primary" onClick={onToCopies}>
            去写文案 <RightOutlined />
          </Button>
        )}
      </Flex>

      <Row gutter={[16, 16]}>
        {s.jobs.map((job, i) => {
          const derived = !!job.derived_from
          const editing = editingSet.has(i)
          const master = derived ? masterOf(job) : undefined
          return (
            <Col key={i} xs={24} sm={12} xl={8}>
              <Card
                size="small"
                title={
                  <Space size={6}>
                    <Typography.Text ellipsis style={{ maxWidth: 220 }} title={jobTitle(job)}>
                      {job.sub_scene}
                    </Typography.Text>
                    <Tag>{job.ratio}</Tag>
                    {derived && <Tooltip title={`由 4:5 母版改尺寸，内容与母版一致`}><Tag color="purple">派生</Tag></Tooltip>}
                  </Space>
                }
                extra={
                  job.image_url && !editing && !pipelineRunning ? (
                    <Space size={0}>
                      <Tooltip title="提示词">
                        <Button type="text" size="small" icon={<FileTextOutlined />} onClick={() => setPromptIdx(i)} disabled={derived} />
                      </Tooltip>
                      <Tooltip title="版本历史">
                        <Button
                          type="text" size="small" icon={<HistoryOutlined />}
                          onClick={() => setVersionIdx(i)}
                          disabled={(job.hist_urls?.length ?? 0) < 2}
                        />
                      </Tooltip>
                      <Popconfirm
                        title={derived ? '用母版当前版本重新改尺寸？' : '重新生成会替换当前图片（旧版进历史）'}
                        onConfirm={() => void call(() => api.regenerateImage(run, i), '已提交重新生成')}
                      >
                        <Tooltip title={derived ? '重新改尺寸' : '重新生成'}>
                          <Button type="text" size="small" icon={<ReloadOutlined />} />
                        </Tooltip>
                      </Popconfirm>
                    </Space>
                  ) : undefined
                }
              >
                {job.image_url ? (
                  <div style={{ position: 'relative' }}>
                    <Image
                      src={job.image_url}
                      style={{ borderRadius: 8, width: '100%', opacity: editing ? 0.5 : 1 }}
                    />
                    {editing && (
                      <Flex
                        align="center" justify="center" vertical gap={8}
                        style={{ position: 'absolute', inset: 0, background: '#ffffffaa', borderRadius: 8 }}
                      >
                        <LoadingOutlined style={{ fontSize: 24, color: '#4f46e5' }} />
                        <Typography.Text type="secondary">后台修改中，可先操作其它图</Typography.Text>
                      </Flex>
                    )}
                    {!editing && !pipelineRunning && (
                      <Button
                        icon={<CommentOutlined />}
                        size="small"
                        style={{ position: 'absolute', right: 8, bottom: 8, background: '#ffffffd9' }}
                        onClick={() => setChatIdx(i)}
                      >
                        修改这张图
                      </Button>
                    )}
                  </div>
                ) : pipelineRunning ? (
                  <div>
                    <Skeleton.Image active style={{ width: '100%', height: 200 }} />
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>生成中…</Typography.Text>
                  </div>
                ) : derived ? (
                  <Flex vertical align="center" gap={10} style={{ padding: '32px 0' }}>
                    {master?.image_url ? (
                      <>
                        <Typography.Text type="secondary">母版已就绪，待改尺寸</Typography.Text>
                        <Button icon={<EditOutlined />} onClick={() => void call(() => api.regenerateImage(run, i), '已提交改尺寸')}>
                          由 4:5 母版改尺寸
                        </Button>
                      </>
                    ) : (
                      <Typography.Text type="secondary">等待 4:5 母版生成后改尺寸</Typography.Text>
                    )}
                  </Flex>
                ) : (
                  <Flex vertical align="center" gap={10} style={{ padding: '32px 0' }}>
                    <Typography.Text type="secondary">尚未生成（可能上次失败）</Typography.Text>
                    <Button icon={<ReloadOutlined />} onClick={() => void call(() => api.regenerateImage(run, i), '已提交生成')}>
                      生成这张
                    </Button>
                  </Flex>
                )}
              </Card>
            </Col>
          )
        })}
      </Row>

      {/* 对话修改（后台并发） */}
      {chatIdx != null && s.jobs[chatIdx] && (
        <ChatDrawer
          open
          onClose={() => setChatIdx(null)}
          title={`修改图片 · ${s.jobs[chatIdx].sub_scene}（${s.jobs[chatIdx].ratio}）`}
          subtitle="在当前图基础上按意见重绘（后台运行，多张图可同时改）；历史版本随时可回跳。"
          history={s.chats[`chat_image_${chatIdx}`] ?? []}
          placeholder="例：背景换成海边；把产品放大一点；整体调亮…"
          onSend={async (fb) => {
            await api.editImage(run, chatIdx, fb)
            await qc.invalidateQueries({ queryKey: runKey(run) })
          }}
        />
      )}

      {/* 提示词编辑 + 对话修改 */}
      {promptIdx != null && s.jobs[promptIdx] && (
        <Drawer
          open
          onClose={() => setPromptIdx(null)}
          title={`提示词 · ${s.jobs[promptIdx].sub_scene}（${s.jobs[promptIdx].ratio}）`}
          width={560}
        >
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            此文本将原样直发生图模型；修改后点该图的「重新生成」生效。
          </Typography.Text>
          <Input.TextArea
            value={promptText}
            onChange={(ev) => setPromptText(ev.target.value)}
            autoSize={{ minRows: 10, maxRows: 16 }}
            style={{ margin: '10px 0', fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 12 }}
          />
          <Button
            type="primary"
            disabled={promptText === s.jobs[promptIdx].image_prompt}
            onClick={() =>
              void call(() => api.patchJob(run, promptIdx, { image_prompt: promptText }), '提示词已保存')
            }
          >
            保存提示词
          </Button>
          <Typography.Title level={5} style={{ marginTop: 20 }}>让 AI 修改提示词</Typography.Title>
          <ChatPanel
            history={s.chats[`chat_prompt_${promptIdx}`] ?? []}
            placeholder="例：光线改成黄昏；构图更聚焦人物；产品再突出一点…"
            maxHeight={260}
            onSend={async (fb) => {
              await api.refinePrompt(run, promptIdx, fb)
              await qc.invalidateQueries({ queryKey: runKey(run) })
            }}
          />
        </Drawer>
      )}

      {/* 版本历史 */}
      {versionIdx != null && s.jobs[versionIdx] && (
        <VersionModal run={run} index={versionIdx} job={s.jobs[versionIdx]} open onClose={() => setVersionIdx(null)} />
      )}
    </div>
  )
}
