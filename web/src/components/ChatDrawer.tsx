// 通用 AI 修改对话：ChatPanel（气泡历史 + 输入框，可内嵌任何容器）
// 与 ChatDrawer（右侧抽屉包装）。历史存服务端 state.chats，发送成功后由调用方刷新。
import { SendOutlined } from '@ant-design/icons'
import { App, Button, Drawer, Empty, Input, Space, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'

import type { ChatMsg } from '../api/types'

interface PanelProps {
  history: ChatMsg[]
  placeholder?: string
  /** 发送修改意见；抛错时展示错误信息 */
  onSend: (feedback: string) => Promise<void>
  /** 历史区最大高度（内嵌模式用；抽屉模式撑满） */
  maxHeight?: number
}

export function ChatPanel({ history, placeholder, onSend, maxHeight }: PanelProps) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const { message } = App.useApp()

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [history, sending])

  const send = async () => {
    const fb = text.trim()
    if (!fb || sending) return
    setSending(true)
    try {
      await onSend(fb)
      setText('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: maxHeight ? undefined : '100%', minHeight: 0 }}>
      <div ref={listRef} style={{ flex: maxHeight ? undefined : 1, maxHeight, overflowY: 'auto', padding: '4px 2px' }}>
        {history.length === 0 ? (
          <Empty
            description="输入修改意见，AI 会在当前结果基础上修改"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ margin: '24px 0' }}
          />
        ) : (
          history.map((m, idx) => (
            <div
              key={idx}
              style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 10 }}
            >
              <div
                style={{
                  maxWidth: '85%',
                  padding: '8px 12px',
                  borderRadius: 12,
                  fontSize: 13,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  background: m.role === 'user' ? '#4f46e5' : '#f2f3f7',
                  color: m.role === 'user' ? '#fff' : 'inherit',
                }}
              >
                {m.content}
              </div>
            </div>
          ))
        )}
        {sending && <Typography.Text type="secondary" style={{ fontSize: 12 }}>AI 修改中…</Typography.Text>}
      </div>
      <Space.Compact style={{ width: '100%', marginTop: 8 }}>
        <Input.TextArea
          autoSize={{ minRows: 1, maxRows: 4 }}
          value={text}
          onChange={(ev) => setText(ev.target.value)}
          placeholder={placeholder ?? '输入修改意见…'}
          onPressEnter={(ev) => {
            if (!ev.shiftKey) {
              ev.preventDefault()
              void send()
            }
          }}
          disabled={sending}
        />
        <Button type="primary" icon={<SendOutlined />} onClick={() => void send()} loading={sending} />
      </Space.Compact>
    </div>
  )
}

interface DrawerProps extends PanelProps {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
}

export default function ChatDrawer({ open, onClose, title, subtitle, ...panel }: DrawerProps) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={title}
      width={440}
      styles={{ body: { display: 'flex', flexDirection: 'column', padding: 16 } }}
    >
      {subtitle && (
        <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
          {subtitle}
        </Typography.Text>
      )}
      <ChatPanel {...panel} />
    </Drawer>
  )
}
