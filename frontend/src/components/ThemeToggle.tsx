import { useCallback, useRef } from 'react'
import { useTheme } from '../lib/themeContext'

// Theme toggle button with a clean sun/moon SVG icon (not emoji, and not Facets logo)
// When clicked, a dynamic circular wave transition emerges from the button and covers the entire page.
export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const buttonRef = useRef<HTMLButtonElement>(null)

  const toggle = useCallback((e?: React.MouseEvent<HTMLButtonElement>) => {
    const next = theme === 'dark' ? 'light' : 'dark'

    // Calculate exact button center and boundary radius
    const el = buttonRef.current || (e?.currentTarget as HTMLButtonElement | null)
    let x = window.innerWidth - 48
    let y = 32
    let initialRadius = 18

    if (el) {
      const rect = el.getBoundingClientRect()
      x = Math.round(rect.left + rect.width / 2)
      y = Math.round(rect.top + rect.height / 2)
      initialRadius = Math.round(rect.width / 2)
    }

    const endRadius = Math.ceil(
      Math.hypot(
        Math.max(x, window.innerWidth - x),
        Math.max(y, window.innerHeight - y)
      )
    )

    // Check if browser supports document.startViewTransition
    const doc = document as unknown as {
      startViewTransition?: (cb: () => void) => { ready: Promise<void> }
    }

    if (doc.startViewTransition) {
      // Remove any previously injected dynamic theme transition style tag
      const existingStyle = document.getElementById('theme-transition-dynamic-style')
      if (existingStyle) {
        existingStyle.remove()
      }

      const style = document.createElement('style')
      style.id = 'theme-transition-dynamic-style'
      style.textContent = `
        ::view-transition-old(root) {
          animation: none !important;
          z-index: 1 !important;
        }
        ::view-transition-new(root) {
          animation: theme-circle-grow 650ms cubic-bezier(0.22, 1, 0.36, 1) forwards !important;
          mix-blend-mode: normal !important;
          z-index: 999999 !important;
        }
        @keyframes theme-circle-grow {
          0% {
            clip-path: circle(0px at ${x}px ${y}px);
          }
          12% {
            clip-path: circle(${initialRadius}px at ${x}px ${y}px);
          }
          100% {
            clip-path: circle(${endRadius}px at ${x}px ${y}px);
          }
        }
      `
      document.head.appendChild(style)

      const transition = doc.startViewTransition(() => {
        setTheme(next)
      })

      transition.ready.finally(() => {
        // Clean up style tag after animation completes
        setTimeout(() => {
          const s = document.getElementById('theme-transition-dynamic-style')
          if (s) s.remove()
        }, 750)
      })
    } else {
      setTheme(next)
    }
  }, [theme, setTheme])

  return (
    <>
      {/* Round Theme Button */}
      <button
        ref={buttonRef}
        onClick={toggle}
        className={`w-9 h-9 rounded-full border flex items-center justify-center transition-transform duration-200 hover:scale-105 active:scale-95 cursor-pointer shadow-sm
          ${theme === 'light'
            ? 'bg-white border-gray-300 text-amber-500 hover:border-gray-400'
            : 'bg-white/5 border-white/15 text-indigo-300 hover:border-white/30 hover:bg-white/10'}`}
        aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      >
        {theme === 'dark' ? (
          /* Moon icon */
          <svg className="w-4 h-4 text-indigo-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
          </svg>
        ) : (
          /* Sun icon */
          <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
          </svg>
        )}
      </button>
    </>
  )
}
