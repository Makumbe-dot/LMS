import { useCallback, useEffect, useState } from 'react'

const KEY = 'lms_theme'

/** Theme is 'system' | 'light' | 'dark'. 'system' leaves the OS preference in charge. */
export function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem(KEY) || 'system')

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    localStorage.setItem(KEY, theme)
  }, [theme])

  const cycle = useCallback(() => {
    setTheme((current) =>
      current === 'system' ? 'light' : current === 'light' ? 'dark' : 'system',
    )
  }, [])

  return { theme, setTheme, cycle }
}
