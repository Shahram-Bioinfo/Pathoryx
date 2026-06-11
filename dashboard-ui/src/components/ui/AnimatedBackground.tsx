/*
 * AnimatedBackground — adapts to both Modern and LCARS themes.
 *
 * Modern mode: three offset nebula blobs (71 s / 89 s / 109 s near-prime cycles)
 * with LCM ≈ 152 hours — they never perceptibly synchronise. GPU-composited.
 *
 * LCARS mode: deep-space star field (three radial-gradient layers), subtle
 * orange/teal ambient glows echoing the sidebar elbow, and a scanline overlay.
 * All motion is opacity + transform only — no repaint.
 *
 * prefers-reduced-motion: suppressed via the global 0.01ms CSS override.
 */
import { useTheme } from '../layout/ThemeProvider'

export function AnimatedBackground() {
  const { isLCARS } = useTheme()

  if (isLCARS) {
    return (
      <div className="fixed inset-0 -z-10 overflow-hidden" aria-hidden>

        {/* Deep space base */}
        <div className="absolute inset-0" style={{ background: '#010B1E' }} />

        {/* Star field — layer 1: fine grid of small stars */}
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: 'radial-gradient(circle, rgba(255,255,255,0.78) 1px, transparent 1px)',
            backgroundSize:  '64px 64px',
            backgroundPosition: '0 0',
            opacity: 0.22,
          }}
        />

        {/* Star field — layer 2: medium stars, offset from layer 1 */}
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: 'radial-gradient(circle, rgba(200,215,255,0.88) 1px, transparent 1px)',
            backgroundSize:  '128px 112px',
            backgroundPosition: '32px 48px',
            opacity: 0.16,
          }}
        />

        {/* Star field — layer 3: sparse bright stars */}
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: 'radial-gradient(circle, rgba(255,255,255,0.95) 1.5px, transparent 1.5px)',
            backgroundSize:  '256px 208px',
            backgroundPosition: '80px 24px',
            opacity: 0.10,
          }}
        />

        {/* Orange ambient glow — upper-right, echoes command bar elbow */}
        <div
          className="absolute pointer-events-none"
          style={{
            top: '-100px', right: '-120px',
            width: '800px', height: '500px',
            borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(255,153,0,0.07) 0%, rgba(255,153,0,0.02) 50%, transparent 70%)',
            willChange: 'transform',
            animation: 'atmosphericDrift 200s ease-in-out infinite',
          }}
        />

        {/* Secondary orange warmth — upper-left quadrant */}
        <div
          className="absolute pointer-events-none"
          style={{
            top: '-60px', left: '15%',
            width: '500px', height: '380px',
            borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(255,180,0,0.035) 0%, transparent 65%)',
            willChange: 'transform',
            animation: 'atmosphericDrift 260s ease-in-out infinite',
            animationDelay: '-90s',
          }}
        />

        {/* Teal subsystem glow — lower-left */}
        <div
          className="absolute pointer-events-none"
          style={{
            bottom: '-80px', left: '-40px',
            width: '600px', height: '600px',
            borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(0,170,220,0.045) 0%, transparent 68%)',
            willChange: 'transform',
            animation: 'atmosphericDrift 240s ease-in-out infinite',
            animationDelay: '-100s',
          }}
        />

        {/* Purple mid-field accent — LCARS indigo nav echo */}
        <div
          className="absolute pointer-events-none"
          style={{
            top: '35%', right: '20%',
            width: '450px', height: '400px',
            borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(153,102,255,0.030) 0%, transparent 70%)',
            willChange: 'transform',
            opacity: 0.8,
            animation: 'atmosphericDrift 160s ease-in-out infinite',
            animationDelay: '-55s',
          }}
        />

        {/* Deep teal lower-right — structural depth layer */}
        <div
          className="absolute pointer-events-none"
          style={{
            bottom: '10%', right: '-5%',
            width: '380px', height: '340px',
            borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(0,200,170,0.025) 0%, transparent 68%)',
            willChange: 'transform',
            animation: 'atmosphericDrift 300s ease-in-out infinite',
            animationDelay: '-140s',
          }}
        />

        {/* Slow vertical scan sweep */}
        <div className="lc-scan-sweep" style={{ top: '-80px' }} />

        {/* Scanlines — horizontal, very subtle */}
        <div
          className="absolute inset-0 pointer-events-none lc-scanlines"
          style={{ opacity: 0.45 }}
        />

        {/* Vignette — draws focus inward */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background: 'radial-gradient(ellipse 90% 90% at 50% 50%, transparent 44%, rgba(0,3,12,0.74) 100%)',
          }}
        />
      </div>
    )
  }

  // ── Modern mode: three nebula blobs, near-prime cycles ────────────────────
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden" aria-hidden>

      {/* Base gradient — adapts to theme via CSS variable */}
      <div className="absolute inset-0" style={{ background: 'var(--scene-gradient)' }} />

      {/* Dot grid texture */}
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

      {/* Blob C — center-right, 109 s cycle, −52 s offset, ≈55% opacity */}
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

      {/* Vignette — 26 s ambient breathe */}
      <div
        className="absolute inset-0 pointer-events-none animate-ambient-breathe"
        style={{
          background: 'radial-gradient(ellipse 90% 90% at 50% 50%, transparent 55%, var(--scene-vignette) 100%)',
        }}
      />
    </div>
  )
}
