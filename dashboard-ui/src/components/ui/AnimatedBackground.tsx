/*
 * AnimatedBackground — scene layers that adapt to both themes via CSS vars.
 *
 * Motion design rationale:
 *
 * The three nebula blobs use near-prime cycle durations (71 s, 89 s, 109 s).
 * Their LCM is 547 781 s ≈ 152 hours — they will never perceptibly synchronise.
 * This gives the background the feeling of independent environmental processes
 * rather than a choreographed animation.
 *
 *   Blob A — top-right  — 71 s,   phase  0 s
 *   Blob B — bot-left   — 89 s,   phase −28 s  (offset from original design)
 *   Blob C — mid-right  — 109 s,  phase −52 s  (tertiary, very faint)
 *
 * The vignette overlay uses ambientBreathe (26 s, opacity 0.91→1) to add
 * scene-level breathing. At < 10% opacity variation it is truly subconscious.
 *
 * All motion is opacity + transform only — GPU composited, zero repaint.
 * prefers-reduced-motion suppresses everything via the global 0.01ms override.
 */
export function AnimatedBackground() {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden" aria-hidden>

      {/* Base gradient — adapts to theme via CSS variable */}
      <div className="absolute inset-0" style={{ background: 'var(--scene-gradient)' }} />

      {/* Dot grid texture — ~5% opacity, structural not decorative */}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage: 'radial-gradient(circle, var(--scene-dot) 1px, transparent 1px)',
          backgroundSize: '48px 48px',
        }}
      />

      {/* Blob A — top-right, 71 s cycle */}
      <div
        className="absolute pointer-events-none"
        style={{
          top: '-120px', right: '-80px',
          width: '560px', height: '440px',
          borderRadius: '50%',
          background: 'radial-gradient(ellipse, var(--scene-nebula-a) 0%, transparent 68%)',
          willChange: 'transform',
          animation: 'atmosphericDrift 71s ease-in-out infinite',
        }}
      />

      {/* Blob B — bottom-left, 89 s cycle, −28 s offset */}
      <div
        className="absolute pointer-events-none"
        style={{
          bottom: '-160px', left: '-60px',
          width: '480px', height: '560px',
          borderRadius: '50%',
          background: 'radial-gradient(ellipse, var(--scene-nebula-b) 0%, transparent 68%)',
          willChange: 'transform',
          animation: 'atmosphericDrift 89s ease-in-out infinite',
          animationDelay: '-28s',
        }}
      />

      {/* Blob C — center-right, 109 s cycle, −52 s offset, ≈55% opacity of B */}
      <div
        className="absolute pointer-events-none"
        style={{
          top: '25%', right: '15%',
          width: '380px', height: '420px',
          borderRadius: '50%',
          background: 'radial-gradient(ellipse, var(--scene-nebula-a) 0%, transparent 65%)',
          willChange: 'transform',
          opacity: 0.55,
          animation: 'atmosphericDrift 109s ease-in-out infinite',
          animationDelay: '-52s',
        }}
      />

      {/* Vignette — pulls focus to centre; animates at 26 s with ambientBreathe */}
      <div
        className="absolute inset-0 pointer-events-none animate-ambient-breathe"
        style={{
          background: 'radial-gradient(ellipse 90% 90% at 50% 50%, transparent 55%, var(--scene-vignette) 100%)',
        }}
      />
    </div>
  )
}
