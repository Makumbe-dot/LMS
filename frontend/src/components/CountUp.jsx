/* A figure that counts to its value.

   On first paint it runs up from zero; when the value changes under it (the
   dashboard refreshes itself) it moves from the old figure to the new one, so a
   change is seen happening rather than discovered. Screen readers get the final
   figure only, and anyone who asked their device for less motion gets it at once. */
import { useEffect, useRef, useState } from 'react'

const DURATION = 800

const still = () =>
  typeof window === 'undefined' ||
  typeof window.requestAnimationFrame !== 'function' ||
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export default function CountUp({ value, format = String }) {
  const target = Number(value) || 0
  const [shown, setShown] = useState(() => (still() ? target : 0))
  const at = useRef(shown)

  useEffect(() => {
    if (still() || at.current === target) {
      at.current = target
      setShown(target)
      return undefined
    }
    const from = at.current
    const started = performance.now()
    let frame = requestAnimationFrame(function step(now) {
      const progress = Math.min(1, (now - started) / DURATION)
      const eased = 1 - (1 - progress) ** 3
      at.current = progress === 1 ? target : from + (target - from) * eased
      setShown(at.current)
      if (progress < 1) frame = requestAnimationFrame(step)
    })
    return () => cancelAnimationFrame(frame)
  }, [target])

  return (
    <>
      <span aria-hidden="true">{format(shown)}</span>
      <span className="sr-only">{format(target)}</span>
    </>
  )
}
