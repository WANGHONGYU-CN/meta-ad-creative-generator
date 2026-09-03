// 当前任务上下文：任务名存 localStorage，切换后全站生效（顶栏 / 工作台共用）。
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

const KEY = 'meta-creative:current-run'

interface TaskCtx {
  currentRun: string | null
  setCurrentRun: (run: string | null) => void
}

const Ctx = createContext<TaskCtx>({ currentRun: null, setCurrentRun: () => {} })

export function TaskProvider({ children }: { children: ReactNode }) {
  const [currentRun, setRun] = useState<string | null>(() => localStorage.getItem(KEY))
  const setCurrentRun = useCallback((run: string | null) => {
    if (run) localStorage.setItem(KEY, run)
    else localStorage.removeItem(KEY)
    setRun(run)
  }, [])
  const value = useMemo(() => ({ currentRun, setCurrentRun }), [currentRun, setCurrentRun])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useCurrentTask = () => useContext(Ctx)
