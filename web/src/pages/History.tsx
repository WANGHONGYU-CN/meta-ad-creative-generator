// 历史素材：检索所有 run，浏览图片/提示词/文案，一键载入工作台继续编辑。
// 数据直接来自 PostgreSQL（实时），无需索引重建。
import { RightCircleOutlined, SearchOutlined } from '@ant-design/icons'
import { App, Button, Card, Collapse, Empty, Flex, Image, Input, Popover, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { useCurrentTask } from '../store/task'

function RunJobs({ runId }: { runId: number }) {
  const { data } = useQuery({ queryKey: ['historyJobs', runId], queryFn: () => api.historyJobs(runId) })
  if (!data) return <Typography.Text type="secondary">载入中…</Typography.Text>
  return (
    <div>
      {data.run.product_info && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          {data.run.product_info.slice(0, 300)}{data.run.product_info.length > 300 ? '…' : ''}
        </Typography.Paragraph>
      )}
      <Flex gap={16} wrap>
        {data.jobs.map((job) => (
          <Card key={job.id} size="small" style={{ width: 260 }}>
            {job.image_url ? (
              <Image src={job.image_url} style={{ borderRadius: 6, width: '100%' }} />
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>（图片文件不存在）</Typography.Text>
            )}
            <Typography.Paragraph strong style={{ fontSize: 13, margin: '8px 0 2px' }}>
              {job.main_scene} / {job.sub_scene}
            </Typography.Paragraph>
            <Flex gap={4} align="center" wrap>
              <Tag>{job.ratio}</Tag>
              {job.derived_from && <Tag color="purple">派生自母版</Tag>}
              {job.image_prompt && (
                <Popover
                  content={<pre style={{ maxWidth: 480, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{job.image_prompt}</pre>}
                  title="生图提示词"
                >
                  <Button size="small" type="link" style={{ padding: 0 }}>提示词</Button>
                </Popover>
              )}
            </Flex>
            {job.copies.map((c) => (
              <Typography.Paragraph key={c.seq} style={{ fontSize: 12, margin: '6px 0 0' }}>
                <Tag style={{ fontSize: 11 }}>{c.angle}</Tag>
                <Typography.Text strong>{c.headline}</Typography.Text>
                <br />
                <Typography.Text type="secondary">{c.primary_text}</Typography.Text>
              </Typography.Paragraph>
            ))}
          </Card>
        ))}
      </Flex>
    </div>
  )
}

export default function HistoryPage() {
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState('')
  const { message } = App.useApp()
  const nav = useNavigate()
  const { setCurrentRun } = useCurrentTask()

  const { data: runs } = useQuery({
    queryKey: ['history', query],
    queryFn: () => api.historyRuns(query),
  })

  return (
    <div>
      <Flex gap={12} style={{ marginBottom: 16 }}>
        <Input
          prefix={<SearchOutlined style={{ color: '#bbb' }} />}
          placeholder="按产品信息 / 主场景 / 细分场景搜索…"
          allowClear
          value={keyword}
          onChange={(ev) => setKeyword(ev.target.value)}
          onPressEnter={() => setQuery(keyword.trim())}
          style={{ maxWidth: 420 }}
        />
        <Button onClick={() => setQuery(keyword.trim())}>搜索</Button>
      </Flex>

      {!runs?.length ? (
        <Empty description="暂无记录。完成一次工作流后这里会自动出现。" style={{ marginTop: 60 }} />
      ) : (
        <Collapse
          items={runs.map((r) => ({
            key: r.id,
            label: (
              <Flex align="center" gap={10}>
                <Typography.Text strong>{r.dir_name}</Typography.Text>
                <Tag>{r.job_count} 张图</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.updated_at}</Typography.Text>
              </Flex>
            ),
            extra: (
              <Button
                size="small"
                type="link"
                icon={<RightCircleOutlined />}
                onClick={(ev) => {
                  ev.stopPropagation()
                  setCurrentRun(r.dir_name)
                  nav('/')
                  message.success('已载入到工作台')
                }}
              >
                载入工作台
              </Button>
            ),
            children: <RunJobs runId={r.id} />,
          }))}
        />
      )}
    </div>
  )
}
