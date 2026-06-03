import { useEffect, useRef, useState } from 'react'

/*
 * Returns true for ~600ms whenever `value` changes from a non-placeholder
 * previous value. Skips the initial mount and '—' → real-value transition
 * (loading state to data state) to avoid spurious flashes on page load.
 */
export function useFlashOnChange(value: string | number): boolean {
  const prev        = useRef<string | number>(value)
  const initialized = useRef(false)
  const [flashing, setFlashing] = useState(false)

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true
      prev.current = value
      return
    }
    if (prev.current === value) return
    const wasPlaceholder = prev.current === '—'
    prev.current = value
    if (wasPlaceholder) return

    setFlashing(true)
    const t = window.setTimeout(() => setFlashing(false), 600)
    return () => clearTimeout(t)
  }, [value])

  return flashing
}
