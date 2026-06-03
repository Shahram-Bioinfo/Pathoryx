import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { ThemeProvider } from './components/layout/ThemeProvider'
import { FailureCenter } from './pages/FailureCenter'
import { Overview } from './pages/Overview'
import { QueueMonitor } from './pages/QueueMonitor'
import { RecoveryCenter } from './pages/RecoveryCenter'
import { SlideDetail } from './pages/SlideDetail'
import { SlideExplorer } from './pages/SlideExplorer'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    },
  },
})

export default function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Shell>
            <Routes>
              <Route path="/"                    element={<Overview />} />
              <Route path="/slides"              element={<SlideExplorer />} />
              <Route path="/slides/:artifactId"  element={<SlideDetail />} />
              <Route path="/queues"              element={<QueueMonitor />} />
              <Route path="/failures"            element={<FailureCenter />} />
              <Route path="/recovery"            element={<RecoveryCenter />} />
            </Routes>
          </Shell>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
