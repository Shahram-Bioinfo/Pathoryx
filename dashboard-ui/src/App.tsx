import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { ThemeProvider } from './components/layout/ThemeProvider'
import { ComputerCore } from './pages/ComputerCore'
import { ComputerCoreFullscreen } from './pages/ComputerCoreFullscreen'
import { FailureCenter } from './pages/FailureCenter'
import { OperationsCenter } from './pages/OperationsCenter'
import { Overview } from './pages/Overview'
import { QueueMonitor } from './pages/QueueMonitor'
import { RecoveryCenter } from './pages/RecoveryCenter'
import { SlideDetail } from './pages/SlideDetail'
import { SlideExplorer } from './pages/SlideExplorer'
import { UploadOperations } from './pages/UploadOperations'
import { RoutingControlCenter } from './pages/RoutingControlCenter'
import { Wallboard } from './pages/Wallboard'

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
          <Routes>
            {/* Shell-free immersive routes — render without sidebar/topbar */}
            <Route path="/computer-core/fullscreen" element={<ComputerCoreFullscreen />} />
            <Route path="/wallboard" element={<Wallboard />} />

            {/* All other routes wrapped in Shell */}
            <Route
              path="/*"
              element={
                <Shell>
                  <Routes>
                    <Route path="/"                    element={<Overview />} />
                    <Route path="/slides"              element={<SlideExplorer />} />
                    <Route path="/slides/:artifactId"  element={<SlideDetail />} />
                    <Route path="/queues"              element={<QueueMonitor />} />
                    <Route path="/failures"            element={<FailureCenter />} />
                    <Route path="/recovery"            element={<RecoveryCenter />} />
                    <Route path="/operations"          element={<OperationsCenter />} />
                    <Route path="/uploads"             element={<UploadOperations />} />
                    <Route path="/computer-core"       element={<ComputerCore />} />
                    <Route path="/routing"            element={<RoutingControlCenter />} />
                  </Routes>
                </Shell>
              }
            />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
