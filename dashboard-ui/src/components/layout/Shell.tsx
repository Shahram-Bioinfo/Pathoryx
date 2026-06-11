import type { ReactNode } from 'react'
import { AnimatedBackground } from '../ui/AnimatedBackground'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { useTheme } from './ThemeProvider'

export function Shell({ children }: { children: ReactNode }) {
  const { isLCARS } = useTheme()

  // LCARS topbar is 96px (h-24); modern is 48px (h-12)
  const mainPt = isLCARS ? 'pt-24' : 'pt-12'

  return (
    <div className="min-h-screen">
      <AnimatedBackground />
      <Sidebar />
      <TopBar />
      <main className={`ml-60 ${mainPt} min-h-screen`}>
        {isLCARS ? (
          <div className="lc-console-root">
            {children}
          </div>
        ) : (
          <div className="p-7 max-w-[1440px] mx-auto animate-entry">
            {children}
          </div>
        )}
      </main>
    </div>
  )
}
