// 提示词管理：6 套提示词按 主流程/分支 分组，抽屉内编辑/恢复默认。
import { EditOutlined, UndoOutlined } from '@ant-design/icons'
import { App, Button, Card, Col, Drawer, Flex, Input, Popconfirm, Row, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { PromptItem } from '../api/types'

function PromptCard({ k, item, onEdit }: { k: string; item: PromptItem; onEdit: () => void }) {
  return (
    <Card
      size="small"
      title={item.name}
      extra={<Button size="small" type="text" icon={<EditOutlined />} onClick={onEdit}>编辑</Button>}
      style={{ height: '100%' }}
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, minHeight: 36 }}>
        {item.description}
      </Typography.Paragraph>
      <Flex gap={4} wrap>
        {item.variables.map((v) => (
          <Tag key={v} style={{ fontSize: 11, fontFamily: 'ui-monospace, monospace' }}>{`{${v}}`}</Tag>
        ))}
      </Flex>
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>key: {k}</Typography.Text>
    </Card>
  )
}

export default function PromptsPage() {
  const { data, refetch } = useQuery({ queryKey: ['prompts'], queryFn: api.getPrompts })
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [editKey, setEditKey] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)

  const item = editKey && data ? data.prompts[editKey] : null

  useEffect(() => {
    if (item) setText(item.template)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editKey])

  if (!data) return null

  const section = (title: string, keys: string[]) => (
    <div style={{ marginBottom: 24 }}>
      <Typography.Title level={5}>{title}</Typography.Title>
      <Row gutter={[16, 16]}>
        {keys.map((k) => (
          <Col key={k} xs={24} md={12} xl={8}>
            <PromptCard k={k} item={data.prompts[k]} onEdit={() => setEditKey(k)} />
          </Col>
        ))}
      </Row>
    </div>
  )

  return (
    <div>
      <Typography.Paragraph type="secondary">
        模板中的 <Typography.Text code>{'{变量名}'}</Typography.Text> 会在运行时自动替换，请保留需要的变量占位符。
      </Typography.Paragraph>
      {section('主流程：① 场景挖掘 → ② 生图总提示词（变量直填，原样直发生图模型）→ ③ 看图写文案', data.main_keys)}
      {section('分支功能：改尺寸 / 结果修改 / 图片修改', data.branch_keys)}

      <Drawer
        open={!!editKey}
        onClose={() => setEditKey(null)}
        title={item ? `${item.name} — ${item.description}` : ''}
        width={720}
        extra={
          <Flex gap={8}>
            <Popconfirm
              title="恢复为内置默认模板？当前修改会丢失"
              onConfirm={async () => {
                if (!editKey) return
                const res = await api.resetPrompt(editKey)
                setText(res.prompt.template)
                message.success('已恢复默认')
                void refetch()
              }}
            >
              <Button icon={<UndoOutlined />}>恢复默认</Button>
            </Popconfirm>
            <Button
              type="primary"
              loading={saving}
              disabled={!item || text === item.template}
              onClick={async () => {
                if (!editKey) return
                setSaving(true)
                try {
                  await api.savePrompt(editKey, text)
                  message.success('已保存')
                  await qc.invalidateQueries({ queryKey: ['prompts'] })
                } catch (err) {
                  message.error(err instanceof Error ? err.message : String(err))
                } finally {
                  setSaving(false)
                }
              }}
            >
              保存
            </Button>
          </Flex>
        }
      >
        {item && (
          <>
            <Flex gap={4} wrap style={{ marginBottom: 10 }}>
              {item.variables.map((v) => (
                <Tag key={v} style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{`{${v}}`}</Tag>
              ))}
            </Flex>
            <Input.TextArea
              value={text}
              onChange={(ev) => setText(ev.target.value)}
              autoSize={{ minRows: 24, maxRows: 32 }}
              style={{ fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 12.5 }}
            />
          </>
        )}
      </Drawer>
    </div>
  )
}
