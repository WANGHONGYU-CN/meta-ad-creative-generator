import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import AppRoutes from './AppRoutes'
import { TaskProvider } from './store/task'
import { appTheme } from './theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 3000 },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={queryClient}>
    <ConfigProvider locale={zhCN} theme={appTheme}>
      <AntApp>
        <TaskProvider>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
        </TaskProvider>
      </AntApp>
    </ConfigProvider>
  </QueryClientProvider>,
)
