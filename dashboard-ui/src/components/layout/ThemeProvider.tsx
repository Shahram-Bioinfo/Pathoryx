import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

export type UITheme = 'modern' | 'lcars' | 'light'

interface ThemeCtx {
  theme: UITheme
  toggle: () => void
  setTheme: (t: UITheme) => void
  isLCARS: boolean
  isLight: boolean
}

const Ctx = createContext<ThemeCtx>({
  theme: 'lcars',
  toggle: () => {},
  setTheme: () => {},
  isLCARS: true,
  isLight: false,
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<UITheme>(() => {
    const s = localStorage.getItem('palantir-theme')
    if (s === 'modern') return 'modern'
    if (s === 'light')  return 'light'
    return 'lcars'
  })

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('dark', 'theme-lcars')
    if (theme === 'lcars') {
      root.classList.add('dark', 'theme-lcars')
    } else if (theme === 'modern') {
      root.classList.add('dark')
    }
    // light: no class — :root variables apply (clinical white palette)
    localStorage.setItem('palantir-theme', theme)
  }, [theme])

  const toggle   = () => setThemeState(t => t === 'lcars' ? 'modern' : 'lcars')
  const setTheme = (t: UITheme) => setThemeState(t)

  return (
    <Ctx.Provider value={{ theme, toggle, setTheme, isLCARS: theme === 'lcars', isLight: theme === 'light' }}>
      {children}
    </Ctx.Provider>
  )
}

export const useTheme = () => useContext(Ctx)
