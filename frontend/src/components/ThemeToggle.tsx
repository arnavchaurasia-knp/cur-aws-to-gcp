import { useState, useEffect, useCallback } from 'react'
import { useTheme } from '../lib/themeContext'
import facetsFIcon from '../assets/facets-logo-f.svg'

// Round favicon-based theme toggle with circle transition animation.
// When toggling, a white circle emerges from the icon and covers the page,
// then the reverse happens for the dark part.
export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const [animating, setAnimating] = useState(false)
  const [coverDirection, setCoverDirection] = useState<'in' | 'out' | null>(null)

  const toggle = useCallback(() => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setCoverDirection('in')
    setAnimating(true)
    // After the cover circle expands, flip the theme and start contracting.
    setTimeout(() => {
      setTheme(next)
      setCoverDirection('out')
    }, 300)
    // After contraction completes, allow next toggle.
    setTimeout(() => {
      setAnimating(false)
      setCoverDirection(null)
    }, 600)
  }, [theme, setTheme])

  return (
    <div className="relative inline-block w-10 h-10">
      {/* Cover circle: emerges from the icon and covers the page during transition */}
      {coverDirection && (
        <div
          className={`absolute inset-0 rounded-full ${
            coverDirection === 'in'
              ? 'bg-white animate-[circle-in_300ms_ease-in-out_forwards]'
              : 'bg-[#0a0a0f] animate-[circle-out_300ms_ease-in-out_forwards]'
          }`}
          style={{
            clipPath: coverDirection === 'in' ? 'circle(0% at 50% 50%)' : 'circle(150% at 50% 50%)',
            transition: 'clip-path 300ms ease-in-out',
          }}
        />
      )}
      {/* The actual round logo/favicon button */}
      <button
        onClick={toggle}
        disabled={animating}
        className={`w-10 h-10 rounded-full border border-white/10 flex items-center justify-center
          transition-all duration-300 hover:scale-110 active:scale-95
          ${animating ? 'opacity-50 pointer-events-none' : ''}
          ${theme === 'light' ? 'bg-white text-[#0a0a0f]' : 'bg-white/5 text-white border-white/20'}`}
        aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      >
        <img
          src={facetsFIcon}
          alt="Facets"
          className="w-6 h-6"
          style={{ filter: theme === 'light' ? 'invert(1)' : 'none' }}
        />
      </button>
      <style>{`
        @keyframes circle-in {
          to { clip-path: circle(150% at 50% 50%); }
        }
        @keyframes circle-out {
          to { clip-path: circle(0% at 50% 50%); }
        }
      `}</style>
    </div>
  )
}
