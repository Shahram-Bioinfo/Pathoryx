// Design-system colour mappings — all badge classes use dark: prefix
// so they respond to the class-based theme toggle on <html>.

export type BadgeVariant =
  | 'default' | 'cyan' | 'indigo' | 'emerald'
  | 'amber' | 'rose' | 'violet' | 'teal' | 'slate' | 'fuchsia'

const STATUS_MAP: Record<string, BadgeVariant> = {
  detected:          'cyan',
  intake_running:    'cyan',
  intake_registered: 'teal',
  qc_pending:        'indigo',
  qc_running:        'indigo',
  qc_passed:         'emerald',
  qc_failed:         'rose',
  dicom_pending:     'violet',
  dicom_running:     'violet',
  dicom_done:        'violet',
  dicom_failed:      'rose',
  upload_pending:    'teal',
  upload_running:    'teal',
  uploaded:          'emerald',
  upload_failed:     'rose',
  pending:           'amber',
  running:           'cyan',
  completed:         'emerald',
  failed:            'rose',
  active:            'emerald',
  stale:             'amber',
  crashed:           'rose',
  stopped:           'slate',
  linked:            'cyan',
  unlinked:          'slate',
  reviewed:          'indigo',
  requeued:          'teal',
  dismissed:         'slate',
}

export function statusVariant(status: string | null | undefined): BadgeVariant {
  if (!status) return 'slate'
  return STATUS_MAP[status] ?? (status.endsWith('_failed') ? 'rose' : 'slate')
}

// Each class string has light + dark: variants so both themes look correct.
export const BADGE_CLASSES: Record<BadgeVariant, string> = {
  default:  'bg-slate-100    text-slate-600    border border-slate-200    dark:bg-slate-800/60  dark:text-slate-300  dark:border-slate-700/50',
  cyan:     'bg-sky-50       text-sky-700      border border-sky-200      dark:bg-cyan-950/60   dark:text-cyan-300   dark:border-cyan-800/40',
  indigo:   'bg-indigo-50    text-indigo-700   border border-indigo-200   dark:bg-indigo-950/60 dark:text-indigo-300 dark:border-indigo-800/40',
  emerald:  'bg-emerald-50   text-emerald-700  border border-emerald-200  dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800/40',
  amber:    'bg-amber-50     text-amber-700    border border-amber-200    dark:bg-amber-950/60  dark:text-amber-300  dark:border-amber-800/40',
  rose:     'bg-rose-50      text-rose-700     border border-rose-200     dark:bg-rose-950/60   dark:text-rose-300   dark:border-rose-800/40',
  violet:   'bg-violet-50    text-violet-700   border border-violet-200   dark:bg-violet-950/60 dark:text-violet-300 dark:border-violet-800/40',
  teal:     'bg-teal-50      text-teal-700     border border-teal-200     dark:bg-teal-950/60   dark:text-teal-300   dark:border-teal-800/40',
  slate:    'bg-slate-100    text-slate-500    border border-slate-200    dark:bg-slate-900/60  dark:text-slate-400  dark:border-slate-700/40',
  fuchsia:  'bg-fuchsia-50   text-fuchsia-700  border border-fuchsia-200  dark:bg-fuchsia-950/60 dark:text-fuchsia-300 dark:border-fuchsia-800/40',
}

// Indicator dot colour classes — light friendly in light mode
export const DOT_CLASSES: Record<BadgeVariant, string> = {
  default:  'bg-slate-400',
  cyan:     'bg-sky-500     dark:bg-cyan-400',
  indigo:   'bg-indigo-500  dark:bg-indigo-400',
  emerald:  'bg-emerald-500 dark:bg-emerald-400',
  amber:    'bg-amber-500   dark:bg-amber-300',
  rose:     'bg-rose-500    dark:bg-rose-400',
  violet:   'bg-violet-500  dark:bg-violet-400',
  teal:     'bg-teal-500    dark:bg-teal-400',
  slate:    'bg-slate-400   dark:bg-slate-500',
  fuchsia:  'bg-fuchsia-500 dark:bg-fuchsia-400',
}

// Timeline dot hex — read from CSS variable at runtime for theme awareness
export const DOT_HEX_LIGHT: Record<BadgeVariant, string> = {
  default: '#94a3b8', cyan:    '#0891b2', indigo:  '#4338ca',
  emerald: '#059669', amber:   '#d97706', rose:    '#e11d48',
  violet:  '#7c3aed', teal:    '#0d9488', slate:   '#64748b', fuchsia: '#a21caf',
}
export const DOT_HEX_DARK: Record<BadgeVariant, string> = {
  default: '#94a3b8', cyan:    '#22D3EE', indigo:  '#818CF8',
  emerald: '#34D399', amber:   '#FCD34D', rose:    '#FB7185',
  violet:  '#C084FC', teal:    '#2DD4BF', slate:   '#64748b', fuchsia: '#E879F9',
}

// Mid-range chart colours — legible on BOTH light and dark backgrounds.
// Recharts requires actual hex values (not CSS vars), so we expose a getter
// that reads from computed styles at call time.
export function getChartColors(): typeof CHART_COLORS_DARK {
  if (typeof document !== 'undefined' &&
      document.documentElement.classList.contains('dark')) {
    return CHART_COLORS_DARK
  }
  return CHART_COLORS_LIGHT
}

export const CHART_COLORS_LIGHT = {
  cyan:    '#0891b2',
  teal:    '#0d9488',
  indigo:  '#4338ca',
  emerald: '#059669',
  amber:   '#d97706',
  rose:    '#e11d48',
  violet:  '#7c3aed',
  slate:   '#64748b',
}

export const CHART_COLORS_DARK = {
  cyan:    '#22D3EE',
  teal:    '#2DD4BF',
  indigo:  '#818CF8',
  emerald: '#34D399',
  amber:   '#FCD34D',
  rose:    '#FB7185',
  violet:  '#C084FC',
  slate:   '#64748b',
}

// Alias for callers that don't need dynamic switching (defaults to DARK for compat)
export const CHART_COLORS = CHART_COLORS_DARK

export function serviceChartColor(name: string, isDark = true): string {
  const map = isDark ? CHART_COLORS_DARK : CHART_COLORS_LIGHT
  const SERVICE: Record<string, keyof typeof map> = {
    babelshark:      'cyan',
    qc_service:      'indigo',
    qc:              'indigo',
    dicom_service:   'violet',
    dicom:           'violet',
    dicomizer:       'violet',
    upload_service:  'teal',
    uploader:        'teal',
    recovery_sentry: 'amber',
    failed_watcher:  'amber',
  }
  return map[SERVICE[name.toLowerCase()] ?? 'slate']
}
