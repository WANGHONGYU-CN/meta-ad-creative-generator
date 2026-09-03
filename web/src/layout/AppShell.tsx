// 全局框架：左侧图标导航 + 顶栏（当前任务切换、后台状态灯）。
import {
  AppstoreOutlined, ClockCircleOutlined, FileTextOutlined, LoadingOutlined,
  PictureOutlined, PlusOutlined, SettingOutlined, ThunderboltFilled,
} from '@ant-design/icons'
import { App, Button, Layout, Menu, Select, Space, Tag, Tooltip, Typography } from 'antd'
import { useMemo } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { useRuns } from '../api/hooks'
import { useCurrentTask } from '../store/task'

const { Sider, Header, Content } = Layout

const NAV = [
  { key: '/', icon: <PictureOutlined />, label: '工作台' },
  { key: '/scenes', icon: <AppstoreOutlined />, label: '场景库' },
  { key: '/history', icon: <ClockCircleOutlined />, label: '历史素材' },
  { key: '/prompts', icon: <FileTextOutlined />, label: '提示词' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
]

export default function AppShell() {
  const nav = useNavigate()
  const { pathname } = useLocation()
  const { currentRun, setCurrentRun } = useCurrentTask()
  const { data: runs } = useRuns()
  const { message } = App.useApp()

  const options = useMemo(
    () =>
      (runs ?? []).map((r) => ({
        value: r.name,
        label: (
          <Space size={6}>
            {r.busy && <LoadingOutlined style={{ color: '#4f46e5' }} />}
            <span>{r.name}</span>
          </Space>
        ),
        // 供搜索用
        title: `${r.name} ${r.product_info}`,
      })),
    [runs],
  )

  const busyCount = (runs ?? []).filter((r) => r.busy).length

  const newTask = async () => {
    const res = await api.createRun({})
    setCurrentRun(res.name)
    message.success('已创建新任务')
    nav('/')
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={168} style={{ borderRight: '1px solid #eef0f3' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '18px 20px 12px' }}>
          <ThunderboltFilled style={{ color: '#4f46e5', fontSize: 20 }} />
          <Typography.Text strong style={{ fontSize: 15 }}>素材工厂</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[NAV.find((n) => n.key === pathname)?.key ?? '/']}
          items={NAV}
          onClick={({ key }) => nav(key)}
          style={{ borderInlineEnd: 'none' }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '0 24px',
            borderBottom: '1px solid #eef0f3', position: 'sticky', top: 0, zIndex: 10,
          }}
        >
          <Typography.Text type="secondary">当前任务</Typography.Text>
          <Select
            style={{ minWidth: 340, maxWidth: 520 }}
            placeholder="选择任务，或点右侧新建"
            value={currentRun ?? undefined}
            options={options}
            onChange={(v) => {
              setCurrentRun(v)
              nav('/')
            }}
            showSearch
            optionFilterProp="title"
            popupMatchSelectWidth={false}
          />
          <Tooltip title="新建一个空白任务">
            <Button icon={<PlusOutlined />} onClick={newTask}>新任务</Button>
          </Tooltip>
          <div style={{ flex: 1 }} />
          {busyCount > 0 && (
            <Tag icon={<LoadingOutlined />} color="processing">
              {busyCount} 个任务后台运行中
            </Tag>
          )}
        </Header>
        <Content style={{ padding: 24, maxWidth: 1440, width: '100%', margin: '0 auto' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
