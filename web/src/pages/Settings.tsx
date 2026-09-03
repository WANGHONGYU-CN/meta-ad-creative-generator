// 设置：API key（环境变量优先，页面填写只落本机 config.json）、模型选择、连通性测试。
import { ApiOutlined, CheckCircleFilled, CloseCircleFilled, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { Alert, App, AutoComplete, Button, Card, Col, Flex, Input, Row, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { SettingsPayload } from '../api/types'

type Cfg = SettingsPayload['config']

export default function SettingsPage() {
  const { data, refetch } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<Awaited<ReturnType<typeof api.testConnection>> | null>(null)
  const [models, setModels] = useState<{ llm: string[]; image: string[] } | null>(null)
  const [loadingModels, setLoadingModels] = useState(false)

  useEffect(() => {
    if (data && !cfg) setCfg(data.config)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  const fetchModels = async () => {
    setLoadingModels(true)
    try {
      const res = await api.listModels()
      setModels({ llm: res.llm, image: res.image })
      for (const [side, err] of Object.entries(res.errors)) {
        message.warning(`${side === 'llm' ? 'Claude' : '生图'} 模型列表拉取失败（可手动输入）：${err}`)
      }
    } finally {
      setLoadingModels(false)
    }
  }

  if (!data || !cfg) return null

  const set = (patch: Partial<Cfg>) => setCfg({ ...cfg, ...patch })

  return (
    <div style={{ maxWidth: 860 }}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Key 优先从环境变量读取；此处填写会覆盖环境变量，只保存在本机 config.json（不上传）。"
        description={
          <Space wrap>
            {Object.entries(data.env).map(([name, ok]) => (
              <Tag key={name} icon={ok ? <CheckCircleFilled /> : <CloseCircleFilled />} color={ok ? 'success' : 'default'}>
                {name}
              </Tag>
            ))}
          </Space>
        }
      />

      <Row gutter={16}>
        <Col span={12}>
          <Card title="Claude（挖场景 / 看图写文案 / 对话修改）" size="small">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Input.Password
                addonBefore="API Key"
                value={cfg.anthropic_api_key}
                onChange={(ev) => set({ anthropic_api_key: ev.target.value })}
                placeholder="环境变量已配置时留空即可"
              />
              <Flex gap={8}>
                <AutoComplete
                  style={{ flex: 1 }}
                  value={cfg.claude_model}
                  onChange={(v) => set({ claude_model: v })}
                  options={(models?.llm ?? []).map((m) => ({ value: m }))}
                  placeholder="Claude 模型"
                  filterOption={(input, opt) => (opt?.value ?? '').includes(input)}
                />
                <Button icon={<ReloadOutlined />} loading={loadingModels} onClick={fetchModels}>拉取模型</Button>
              </Flex>
              <Input
                addonBefore="Base URL"
                value={cfg.anthropic_base_url}
                onChange={(ev) => set({ anthropic_base_url: ev.target.value })}
                placeholder="走中转/代理时填写，可选"
              />
            </Space>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="生图（OpenAI 兼容接口）" size="small">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Input.Password
                addonBefore="API Key"
                value={cfg.openai_api_key}
                onChange={(ev) => set({ openai_api_key: ev.target.value })}
                placeholder="环境变量已配置时留空即可"
              />
              <Flex gap={8}>
                <AutoComplete
                  style={{ flex: 1 }}
                  value={cfg.image_model}
                  onChange={(v) => set({ image_model: v })}
                  options={(models?.image ?? []).map((m) => ({ value: m }))}
                  placeholder="生图模型"
                  filterOption={(input, opt) => (opt?.value ?? '').includes(input)}
                />
                <Button icon={<ReloadOutlined />} loading={loadingModels} onClick={fetchModels}>拉取模型</Button>
              </Flex>
              <Input
                addonBefore="Base URL"
                value={cfg.openai_base_url}
                onChange={(ev) => set({ openai_base_url: ev.target.value })}
                placeholder="走中转/代理时填写，可选"
              />
            </Space>
          </Card>
        </Col>
      </Row>

      <Flex gap={12} style={{ marginTop: 16 }}>
        <Button
          type="primary" icon={<SaveOutlined />} loading={saving}
          onClick={async () => {
            setSaving(true)
            try {
              await api.saveSettings(cfg)
              message.success('已保存到 config.json')
              await qc.invalidateQueries({ queryKey: ['settings'] })
              const fresh = await refetch()
              if (fresh.data) setCfg(fresh.data.config)
            } catch (err) {
              message.error(err instanceof Error ? err.message : String(err))
            } finally {
              setSaving(false)
            }
          }}
        >
          保存设置
        </Button>
        <Button
          icon={<ApiOutlined />} loading={testing}
          onClick={async () => {
            setTesting(true)
            setTestResult(null)
            try {
              setTestResult(await api.testConnection())
            } catch (err) {
              message.error(err instanceof Error ? err.message : String(err))
            } finally {
              setTesting(false)
            }
          }}
        >
          测试连接（先保存再测试）
        </Button>
      </Flex>

      {testResult && (
        <Space direction="vertical" style={{ marginTop: 16, width: '100%' }}>
          <Alert
            type={testResult.claude.ok ? 'success' : 'error'}
            showIcon
            message={`Claude：${testResult.claude.ok ? '连接成功' : '连接失败'}`}
            description={<Typography.Text style={{ fontSize: 12 }}>{testResult.claude.message}</Typography.Text>}
          />
          <Alert
            type={testResult.image.ok ? 'success' : 'error'}
            showIcon
            message={`生图接口：${testResult.image.ok ? '连接成功' : '连接失败'}`}
            description={!testResult.image.ok && <Typography.Text style={{ fontSize: 12 }}>{testResult.image.message}</Typography.Text>}
          />
        </Space>
      )}
    </div>
  )
}
