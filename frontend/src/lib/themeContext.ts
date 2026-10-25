import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { getStoredTheme, setStoredTheme, type Theme } from './theme'

const ThemeContext = createContext<{
  theme: Theme
  setTheme: (t: Theme) => void
} | undefined>(undefined)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(getStoredTheme)

  useEffect(() => {
    setStoredTheme(theme)
  }, [theme])

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within a ThemeProvider')
  return ctx
}
