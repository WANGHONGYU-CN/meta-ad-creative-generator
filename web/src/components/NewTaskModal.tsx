// 新建任务弹窗：先选产品（或就地新建产品），再创建任务。
// 产品是一等实体（决策 20）：品牌名/广告语言默认值挂在产品上，建任务时带入。
import { App, Form, Input, Modal, Radio, Select } from 'antd'
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { useCurrentTask } from '../store/task'

export default function NewTaskModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const nav = useNavigate()
  const { setCurrentRun } = useCurrentTask()
  const [mode, setMode] = useState<'pick' | 'create'>('pick')
  const [productId, setProductId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const { data: products } = useQuery({ queryKey: ['products'], queryFn: api.listProducts, enabled: open })

  useEffect(() => {
    if (open) {
      setMode(products?.length ? 'pick' : 'create')
      setProductId(null)
      form.resetFields()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, products?.length])

  const submit = async () => {
    setSaving(true)
    try {
      let pid = productId
      if (mode === 'create') {
        const v = await form.validateFields()
        const p = await api.createProduct(v)
        pid = p.id
        await qc.invalidateQueries({ queryKey: ['products'] })
      }
      if (pid == null) {
        message.warning('请选择产品')
        return
      }
      const res = await api.createRun({ product_id: pid })
      setCurrentRun(res.name)
      await qc.invalidateQueries({ queryKey: ['runs'] })
      message.success('已创建新任务')
      onClose()
      nav('/')
    } catch (err) {
      if (err instanceof Error) message.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onCancel={onClose}
      title="新建任务"
      okText="创建任务"
      confirmLoading={saving}
      onOk={submit}
      destroyOnClose
    >
      <Radio.Group
        value={mode}
        onChange={(ev) => setMode(ev.target.value)}
        style={{ marginBottom: 16 }}
        options={[
          { label: '选择已有产品', value: 'pick', disabled: !products?.length },
          { label: '新建产品', value: 'create' },
        ]}
        optionType="button"
      />
      {mode === 'pick' ? (
        <Select
          style={{ width: '100%' }}
          placeholder="选择产品"
          value={productId ?? undefined}
          onChange={setProductId}
          showSearch
          optionFilterProp="title"
          options={(products ?? []).map((p) => ({
            value: p.id,
            label: `${p.name}（${p.run_count ?? 0} 个任务）`,
            title: `${p.name} ${p.info}`,
          }))}
        />
      ) : (
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="name" label="产品名" rules={[{ required: true, message: '给产品起个短名' }]}>
            <Input placeholder="如：sondo" maxLength={80} />
          </Form.Item>
          <Form.Item name="info" label="产品信息（挖场景的输入，可稍后在工作台填）" initialValue="">
            <Input.TextArea rows={4} placeholder="产品名称、卖点、目标人群、目标市场等，越具体场景越准" />
          </Form.Item>
          <Form.Item name="brand_name" label="默认品牌名（建任务时带入，可按任务改）" initialValue="">
            <Input placeholder="可留空" />
          </Form.Item>
          <Form.Item name="ad_language" label="默认广告语言" initialValue="">
            <Input placeholder="English / 中文 / Español…" />
          </Form.Item>
        </Form>
      )}
    </Modal>
  )
}
