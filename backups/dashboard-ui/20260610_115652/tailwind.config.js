/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
      },
      colors: {
        ops: {
          950: '#020810',
          900: '#040C1A',
          850: '#061222',
          800: '#081830',
          700: '#0D2444',
        },
      },
      // Easing tokens — used via inline style for complex curves
      // Standard: cubic-bezier(0.4, 0, 0.2, 1)
      // Enter:    cubic-bezier(0.0, 0, 0.2, 1)   (decelerate)
      // Spring:   cubic-bezier(0.16, 1, 0.3, 1)
      transitionTimingFunction: {
        'out-expo':    'cubic-bezier(0.16, 1, 0.3, 1)',
        'in-out-quad': 'cubic-bezier(0.45, 0, 0.55, 1)',
        'standard':    'cubic-bezier(0.4, 0, 0.2, 1)',
      },
      transitionDuration: {
        '0':   '0ms',
        '100': '100ms',
        '150': '150ms',
        '200': '200ms',
        '250': '250ms',
        '350': '350ms',
      },
      boxShadow: {
        // Border-only shadows — no spread glow, just definition
        'card':       '0 1px 3px rgba(0,0,0,0.5), 0 0 0 1px rgba(34,211,238,0.07)',
        'card-focus': '0 0 0 1px rgba(34,211,238,0.20)',
        'panel':      '0 8px 24px rgba(0,0,0,0.4), 0 0 0 1px rgba(34,211,238,0.06)',
      },
      animation: {
        'entry':          'entry 350ms cubic-bezier(0.16,1,0.3,1) both',
        'fade-in':        'fadeIn 200ms ease-out both',
        'pulse-status':   'pulseStatus 2.8s ease-in-out infinite',
        'skeleton-sweep': 'skeletonSweep 1.8s ease-in-out infinite',
        // Living Telemetry — transform/opacity only, GPU composited
        'telemetry-dot':      'telemetryDot 3.8s ease-in-out infinite',
        'constellation-ping': 'constellationPing 3.2s ease-out infinite',
        'kpi-flash':          'kpiFlash 500ms ease-out',
        'atmospheric-drift':  'atmosphericDrift 71s ease-in-out infinite',
        // Operational realism additions
        // subsystemBreath: used on live status dots for independent per-subsystem heartbeat
        // Period is set inline (9–13 s range) — this utility supplies the keyframe reference only.
        'subsystem-breath':  'subsystemBreath 11s ease-in-out infinite',
        // ambientBreathe: imperceptibly slow scene vignette oscillation (opacity only)
        'ambient-breathe':   'ambientBreathe 26s ease-in-out infinite',
      },
      keyframes: {
        entry: {
          '0%':   { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeIn: {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
        pulseStatus: {
          '0%, 100%': { opacity: '1',   transform: 'scale(1)' },
          '50%':      { opacity: '0.3', transform: 'scale(0.85)' },
        },
        skeletonSweep: {
          '0%':   { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(300%)' },
        },
        // Particle traveling across a pipeline connector — translateX+opacity, no layout
        telemetryDot: {
          '0%':   { transform: 'translateX(0px)',  opacity: '0' },
          '12%':  { transform: 'translateX(3px)',  opacity: '1' },
          '88%':  { transform: 'translateX(27px)', opacity: '1' },
          '100%': { transform: 'translateX(30px)', opacity: '0' },
        },
        // Service node status ring — scale+opacity, GPU composited via transform-box
        constellationPing: {
          '0%':   { transform: 'scale(1)',    opacity: '0.65' },
          '65%':  { transform: 'scale(1.85)', opacity: '0'    },
          '100%': { transform: 'scale(1.85)', opacity: '0'    },
        },
        // KPI value flash on data update — opacity only, fires once
        kpiFlash: {
          '0%':   { opacity: '1'    },
          '18%':  { opacity: '0.25' },
          '55%':  { opacity: '1'    },
          '100%': { opacity: '1'    },
        },
        // Atmospheric nebula drift — imperceptibly slow (~0.15 px/s)
        atmosphericDrift: {
          '0%, 100%': { transform: 'translate(0px,   0px)'  },
          '33%':      { transform: 'translate(8px,  -5px)'  },
          '66%':      { transform: 'translate(-5px,  7px)'  },
        },
        // Independent subsystem heartbeat — opacity 0.82→1→0.82
        // Used with per-instance duration + delay set inline, so each indicator
        // operates on its own cycle. The range (0.82–1) is narrow enough to be
        // subconscious, not consciously distracting.
        subsystemBreath: {
          '0%, 100%': { opacity: '0.82' },
          '50%':      { opacity: '1'    },
        },
        // Scene vignette breath — imperceptibly slow (26 s cycle, 0.91→1→0.91)
        // Adds subconscious alive-ness to the scene without any element motion.
        ambientBreathe: {
          '0%, 100%': { opacity: '0.91' },
          '50%':      { opacity: '1'    },
        },
      },
    },
  },
  plugins: [],
}
