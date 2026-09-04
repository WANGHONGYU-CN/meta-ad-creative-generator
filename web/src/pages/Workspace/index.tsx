// 工作台：任务制分步面板（产品信息 → 场景 → 图片 → 文案与导出）。
// 后台任务不锁页面：顶部横幅显示进度，完成后 useHarvest 自动收割刷新。
import { BulbOutlined, FileImageOutlined, FormOutlined, InboxOutlined, RocketOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Progress, Skeleton, Steps, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'

import { useHarvest, useRunBundle } from '../../api/hooks'
import NewTaskModal from '../../components/NewTaskModal'
import { useCurrentTask } from '../../store/task'
import StepCopies from './StepCopies'
import StepImages from './StepImages'
import StepInput from './StepInput'
import StepScenes from './StepScenes'

export default function WorkspacePage() {
  const { currentRun } = useCurrentTask()
  const { data: bundle, isLoading, error } = useRunBundle(currentRun)
  const [step, setStep] = useState(0)
  const [newTaskOpen, setNewTaskOpen] = useState(false)
  useHarvest(currentRun, bundle)

  // 切任务时按数据进度定位到最远的一步
  useEffect(() => {
    if (!bundle) return
    const s = bundle.state
    const furthest = s.jobs.some((j) => j.image_url) ? (s.jobs.some((j) => j.copies.length) ? 3 : 2)
      : s.jobs.length ? 2 : s.scenes.length ? 1 : 0
    setStep(furthest)
    // 只在任务切换时重定位，数据轮询刷新不打断用户所在步骤
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRun, !!bundle])

  const enabled = useMemo(() => {
    const s = bundle?.state
    return [true, !!s?.scenes.length, !!s?.jobs.length, !!s?.jobs.some((j) => j.image_url)]
  }, [bundle])

  if (!currentRun) {
    return (
      <Card style={{ maxWidth: 560, margin: '80px auto', textAlign: 'center' }}>
        <Empty description="还没有选择任务" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <Typography.Paragraph type="secondary">
            从顶栏选择一个已有任务，或新建一个开始做素材。
          </Typography.Paragraph>
          <Button type="primary" onClick={() => setNewTaskOpen(true)}>
            新建任务
          </Button>
          <NewTaskModal open={newTaskOpen} onClose={() => setNewTaskOpen(false)} />
        </Empty>
      </Card>
    )
  }

  if (error) return <Alert type="error" message={`任务载入失败：${error.message}`} showIcon />
  if (isLoading || !bundle) return <Skeleton active paragraph={{ rows: 8 }} />

  const { status } = bundle
  const pipeline = status.pipeline
  const editing = Object.values(status.edits).filter((s) => s.state === 'running').length

  return (
    <div>
      {status.mining?.state === 'running' && (
        <Alert
          type="info" showIcon icon={<BulbOutlined />}
          style={{ marginBottom: 16 }}
          message="AI 正在挖掘投放场景（约 1-4 分钟）"
          description="完成后自动刷新；期间可以切到其它任务继续工作。"
        />
      )}
      {pipeline?.state === 'running' && (
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message={pipeline.kind === 'images' ? '正在后台生图' : '正在后台生成文案'}
          description={
            <Progress
              percent={pipeline.total ? Math.round((pipeline.done / pipeline.total) * 100) : 0}
              format={() => `${pipeline.done}/${pipeline.total}`}
              status="active"
            />
          }
        />
      )}
      {editing > 0 && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message={`${editing} 张图正在后台修改，完成后自动更新`} />
      )}

      <Steps
        current={step}
        onChange={(s) => enabled[s] && setStep(s)}
        items={[
          { title: '产品信息', icon: <FormOutlined /> },
          { title: '投放场景', icon: <BulbOutlined />, disabled: !enabled[1] },
          { title: '生成图片', icon: <FileImageOutlined />, disabled: !enabled[2] },
          { title: '文案与导出', icon: <InboxOutlined />, disabled: !enabled[3] },
        ]}
        style={{ marginBottom: 20, maxWidth: 880 }}
      />

      {step === 0 && <StepInput run={currentRun} bundle={bundle} onMined={() => setStep(1)} />}
      {step === 1 && <StepScenes run={currentRun} bundle={bundle} onGenerate={() => setStep(2)} />}
      {step === 2 && <StepImages run={currentRun} bundle={bundle} onToCopies={() => setStep(3)} />}
      {step === 3 && <StepCopies run={currentRun} bundle={bundle} />}

      {pipeline && pipeline.state !== 'running' && pipeline.errors.length > 0 && (
        <Alert
          style={{ marginTop: 16 }}
          type="warning" showIcon icon={<RocketOutlined />}
          message={`上次后台任务有 ${pipeline.errors.length} 个失败项`}
          description={pipeline.errors.map((e, i) => (
            <div key={i} style={{ fontSize: 12 }}>{e}</div>
          ))}
        />
      )}
    </div>
  )
}
