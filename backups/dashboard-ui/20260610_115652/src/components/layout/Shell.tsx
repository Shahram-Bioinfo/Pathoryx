import type { ReactNode } from 'react'
import { AnimatedBackground } from '../ui/AnimatedBackground'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

export function Shell({ children }: { children: ReactNode }) {
  return (
    /* body already sets background/color via CSS vars — no inline override needed */
    <div className="min-h-screen">
      <AnimatedBackground />
      <Sidebar />
      <TopBar />
      <main className="ml-60 pt-12 min-h-screen">
        <div className="p-7 max-w-[1440px] mx-auto animate-entry">
          {children}
        </div>
      </main>
    </div>
  )
}
