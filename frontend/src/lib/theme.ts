export type Theme = 'light' | 'dark'

const THEME_KEY = 'cur-web-theme'

export function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'dark'
  const stored = localStorage.getItem(THEME_KEY) as Theme | null
  if (stored === 'light' || stored === 'dark') return stored
  return 'dark'
}

export function setStoredTheme(theme: Theme) {
  localStorage.setItem(THEME_KEY, theme)
  if (theme === 'light') {
    document.documentElement.classList.remove('dark')
  } else {
    document.documentElement.classList.add('dark')
  }
}

export function toggleTheme(current: Theme): Theme {
  const next: Theme = current === 'dark' ? 'light' : 'dark'
  setStoredTheme(next)
  return next
}
