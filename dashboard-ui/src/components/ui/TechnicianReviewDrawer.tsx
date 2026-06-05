/**
 * TechnicianReviewDrawer — Technician Review & Manual Rename
 *
 * Three tabs:
 *   Inspect Label      — professional label viewer with zoom/pan/fullscreen
 *   Correct Filename   — Quick Rename OR Structured Builder
 *   Audit Trail        — chronological change + event history
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle, ChevronRight, Clock, ImageOff,
  Maximize2, Minimize2, RotateCcw, RotateCw, X, XCircle, ZoomIn, ZoomOut,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchAuditTrail,
  fetchLabelPreview,
  patchReviewState,
  postTechnicianRename,
  postValidateFilename,
} from '../../api/watchFolders'
import type {
  AuditChangeItem,
  AuditEventItem,
  FilenameValidationResponse,
  LabelPreviewResponse,
  MonitoredFileItem,
  TechnicianRenameResponse,
} from '../../types/api'
import { fmtBytes, fmtDatetime, fmtEventType, fmtRelative } from '../../utils/formatters'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SUPPORTED_EXTENSIONS = ['.svs', '.ndpi', '.mrxs', '.tiff', '.tif', '.scn', '.czi', '.vsi', '.bif']

const STAIN_LIST = [
  'H&E', 'HE', 'PAS', 'MT', 'EVG', 'Gomorri', 'Grocott', 'Ziehl', 'Giemsa',
  'AE1/3', 'AE1-3', 'CK7', 'CK5/6', 'CK5-6', 'CK14', 'CK18', 'CK19', 'CK20',
  'CD3', 'CD4', 'CD5', 'CD8', 'CD10', 'CD15', 'CD19', 'CD20', 'CD21', 'CD22',
  'CD23', 'CD30', 'CD31', 'CD34', 'CD38', 'CD43', 'CD45', 'CD56', 'CD57', 'CD61',
  'CD68', 'CD79a', 'CD99', 'CD117', 'CD123', 'CD138', 'CD163', 'CD207',
  'ER', 'PR', 'HER2', 'KI-67', 'P53', 'P63', 'p40', 'p16',
  'BCL2', 'BCL6', 'SOX-11', 'SOX11', 'SOX-10', 'GATA3', 'PAX5', 'PAX8',
  'S100', 'GFAP', 'NSE', 'SYN', 'CHRA', 'VIM', 'Desmin',
  'TTF1', 'NKX3.1', 'PSMA', 'PSA', 'AFP', 'CEA', 'CA125', 'CA19-9',
  'ALK1', 'BRAF', 'EGFR', 'PD-1', 'PD-L1',
  'MLH1', 'MSH2', 'MSH6', 'PMS2',
  'ERG', 'EMA', 'MUC2', 'MUC4', 'SALL4', 'STAT6',
  'TdT', 'MPO', 'MUM1', 'LEF1',
  'IgG4', 'Kappa', 'Lambda',
  'MG', 'PAP', 'MGG', 'FE', 'Afog', 'DIA-PAS', 'NASDCL',
  'CMV', 'SV40', 'HSV1', 'HSV2', 'HHV-8', 'HHV8', 'LMP',
  'ASMA', 'Aktin',
]

// ---------------------------------------------------------------------------
// Review state helpers
// ---------------------------------------------------------------------------

const REVIEW_STATE_LABELS: Record<string, string> = {
  detected: 'Detected', unlinked: 'Unlinked', linked: 'Linked',
  investigating: 'Investigating', corrected: 'Corrected',
  requeued: 'Requeued', reviewed: 'Reviewed', dismissed: 'Dismissed',
}

const REVIEW_STATE_COLOR: Record<string, string> = {
  detected: 'var(--chart-amber)', unlinked: 'var(--chart-amber)',
  investigating: 'var(--accent)', corrected: 'var(--chart-teal)',
  requeued: 'var(--chart-teal)', reviewed: 'var(--text-muted)',
  dismissed: 'var(--text-faint)', linked: 'var(--accent)',
}

// ---------------------------------------------------------------------------
// Hook: load image via fetch to capture X-Label-Source header
// ---------------------------------------------------------------------------

interface LabelImageState {
  blobUrl:  string | null
  source:   string | null
  dims:     [number, number] | null
  status:   'loading' | 'loaded' | 'error'
  setDims:  (d: [number, number]) => void
}

function useLabelImage(imageUrl: string): LabelImageState {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [source, setSource]   = useState<string | null>(null)
  const [dims,   setDims]     = useState<[number, number] | null>(null)
  const [status, setStatus]   = useState<'loading' | 'loaded' | 'error'>('loading')

  useEffect(() => {
    let current = true
    let objUrl:  string | null = null
    setStatus('loading'); setBlobUrl(null); setSource(null); setDims(null)

    fetch(imageUrl)
      .then(r => {
        if (!current) return null
        if (r.headers.get('x-label-source')) setSource(r.headers.get('x-label-source'))
        if (!r.ok) { setStatus('error'); return null }
        return r.blob()
      })
      .then(b => {
        if (!b || !current) return
        objUrl = URL.createObjectURL(b)
        setBlobUrl(objUrl)
        setStatus('loaded')
      })
      .catch(() => { if (current) setStatus('error') })

    return () => {
      current = false
      if (objUrl) URL.revokeObjectURL(objUrl)
    }
  }, [imageUrl])

  return { blobUrl, source, dims, status, setDims }
}

// ---------------------------------------------------------------------------
// Hook: zoom + pan state machine
// ---------------------------------------------------------------------------

const MIN_ZOOM = 0.5
const MAX_ZOOM = 10

interface ZoomPanState {
  zoom: number; pan: { x: number; y: number }; isDragging: boolean
  rotation: number
  handleWheel:     (e: React.WheelEvent) => void
  handleMouseDown: (e: React.MouseEvent) => void
  handleMouseMove: (e: React.MouseEvent) => void
  handleMouseUp:   () => void
  zoomIn: () => void; zoomOut: () => void; reset: () => void
  rotateLeft: () => void; rotateRight: () => void
}

function useZoomPan(): ZoomPanState {
  const [zoom,     setZoom]     = useState(1)
  const [pan,      setPan]      = useState({ x: 0, y: 0 })
  const [rotation, setRotation] = useState(0)
  const drag = useRef<{ mx: number; my: number; px: number; py: number } | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault(); e.stopPropagation()
    const f = e.deltaY < 0 ? 1.15 : 1 / 1.15
    setZoom(z => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * f)))
  }, [])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    e.preventDefault()
    drag.current = { mx: e.clientX, my: e.clientY, px: pan.x, py: pan.y }
    setIsDragging(true)
  }, [pan])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!drag.current) return
    setPan({
      x: drag.current.px + (e.clientX - drag.current.mx),
      y: drag.current.py + (e.clientY - drag.current.my),
    })
  }, [])

  const handleMouseUp = useCallback(() => { drag.current = null; setIsDragging(false) }, [])

  const zoomIn     = useCallback(() => setZoom(z => Math.min(MAX_ZOOM, z * 1.35)), [])
  const zoomOut    = useCallback(() => setZoom(z => Math.max(MIN_ZOOM, z / 1.35)), [])
  const reset      = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }); setRotation(0) }, [])
  const rotateLeft  = useCallback(() => { setRotation(r => (r - 90 + 360) % 360); setZoom(1); setPan({ x: 0, y: 0 }) }, [])
  const rotateRight = useCallback(() => { setRotation(r => (r + 90) % 360); setZoom(1); setPan({ x: 0, y: 0 }) }, [])

  return { zoom, pan, isDragging, rotation, handleWheel, handleMouseDown, handleMouseMove, handleMouseUp, zoomIn, zoomOut, reset, rotateLeft, rotateRight }
}

// ---------------------------------------------------------------------------
// Sub-component: image metadata chips
// ---------------------------------------------------------------------------

function LabelMetaChips({
  source, dims, labelPreview,
}: {
  source: string | null
  dims: [number, number] | null
  labelPreview: LabelPreviewResponse | null | undefined
}) {
  const stain   = labelPreview?.stain_matched ?? labelPreview?.stain_type
  const scanner = labelPreview?.scanner_id

  const chips: Array<{ text: string; color: string; bg?: string }> = [
    source === 'label_crop'
      ? { text: 'label crop', color: 'var(--chart-teal)', bg: 'rgba(52,211,153,0.08)' }
      : source === 'wsi_embedded'
      ? { text: 'wsi extract', color: 'var(--chart-amber)', bg: 'rgba(217,119,6,0.08)' }
      : null,
    dims ? { text: `${dims[0]}×${dims[1]}`, color: 'var(--text-muted)' } : null,
    stain   ? { text: stain,   color: 'var(--accent)' } : null,
    scanner ? { text: scanner, color: 'var(--text-faint)' } : null,
  ].filter(Boolean) as Array<{ text: string; color: string; bg?: string }>

  if (chips.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1 mt-2">
      {chips.map(c => (
        <span
          key={c.text}
          className="text-[8px] font-medium px-1.5 py-0.5 rounded-full"
          style={{
            color: c.color,
            background: c.bg ?? 'var(--surface-inset)',
            border: '1px solid var(--border-faint)',
            letterSpacing: '0.03em',
          }}
        >
          {c.text}
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: professional label image viewer (zoom + pan)
// ---------------------------------------------------------------------------

interface ViewerProps {
  blobUrl:      string | null
  status:       'loading' | 'loaded' | 'error'
  source:       string | null
  dims:         [number, number] | null
  setDims:      (d: [number, number]) => void
  containerH:   number          // viewport height in px
  labelPreview: LabelPreviewResponse | null | undefined
  onExpand?:    () => void       // opens lightbox — omit to disable
  showChips?:   boolean
  autoKeyboard?: boolean         // attach +/-/0/F keyboard listeners
}

function LabelImageViewer({
  blobUrl, status, source, dims, setDims,
  containerH, labelPreview, onExpand, showChips = true, autoKeyboard = false,
}: ViewerProps) {
  const zp            = useZoomPan()
  const containerRef  = useRef<HTMLDivElement>(null)
  const [fsActive, setFsActive] = useState(false)

  // Optional keyboard shortcuts (active in lightbox)
  useEffect(() => {
    if (!autoKeyboard) return
    const fn = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if      (e.key === '+'  || e.key === '=') { e.preventDefault(); zp.zoomIn() }
      else if (e.key === '-')                   { e.preventDefault(); zp.zoomOut() }
      else if (e.key === '0')                   { e.preventDefault(); zp.reset() }
      else if (e.key.toLowerCase() === 'r')     { e.preventDefault(); zp.rotateRight() }
      else if (e.key.toLowerCase() === 'f')     { e.preventDefault(); toggleFs() }
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }) // intentional — re-bind when zoom changes

  // Fullscreen toggle
  const toggleFs = useCallback(() => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen().catch(() => {})
    } else {
      document.exitFullscreen().catch(() => {})
    }
  }, [])

  useEffect(() => {
    const fn = () => setFsActive(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', fn)
    return () => document.removeEventListener('fullscreenchange', fn)
  }, [])

  const cursor =
    status !== 'loaded' ? 'default'
    : zp.isDragging     ? 'grabbing'
    : zp.zoom > 1       ? 'grab'
    : onExpand          ? 'zoom-in'
    : 'default'

  const ctrlBtn = (
    title: string,
    icon: React.ReactNode,
    onClick: () => void,
    active?: boolean,
  ) => (
    <button
      type="button"
      title={title}
      onClick={e => { e.stopPropagation(); onClick() }}
      style={{
        width: 22, height: 22,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        borderRadius: 3,
        background: active ? 'rgba(255,255,255,0.18)' : 'transparent',
        color: 'rgba(255,255,255,0.85)',
        border: 'none',
        cursor: 'pointer',
      }}
    >
      {icon}
    </button>
  )

  return (
    <div ref={containerRef}>
      {/* Viewport */}
      <div
        style={{
          height: containerH,
          borderRadius: 6,
          background: '#0a0a0a',
          overflow: 'hidden',
          position: 'relative',
          cursor,
          userSelect: 'none',
          touchAction: 'none',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
        onWheel={zp.handleWheel}
        onMouseDown={status === 'loaded' ? zp.handleMouseDown : undefined}
        onMouseMove={zp.handleMouseMove}
        onMouseUp={zp.handleMouseUp}
        onMouseLeave={zp.handleMouseUp}
        onClick={status === 'loaded' && !zp.isDragging && zp.zoom <= 1 && onExpand
          ? onExpand : undefined}
      >
        {/* Loading skeleton */}
        {status === 'loading' && (
          <div
            className="absolute inset-0 animate-pulse"
            style={{ background: 'var(--surface-inset)' }}
          />
        )}

        {/* Error state */}
        {status === 'error' && (
          <div
            className="flex flex-col items-center gap-3"
            style={{ color: 'rgba(255,255,255,0.3)', padding: '0 32px', textAlign: 'center' }}
          >
            <ImageOff className="h-8 w-8 opacity-40" />
            <p style={{ fontSize: 11, lineHeight: 1.5, color: 'rgba(255,255,255,0.4)' }}>
              No label image available
            </p>
            <p style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)' }}>
              This file may not have passed through the label extraction stage
            </p>
          </div>
        )}

        {/* Image */}
        {blobUrl && (
          <img
            src={blobUrl}
            alt="Slide label"
            draggable={false}
            onLoad={e => {
              const img = e.currentTarget
              setDims([img.naturalWidth, img.naturalHeight])
            }}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              objectFit: 'contain',
              transform: `rotate(${zp.rotation}deg) scale(${zp.zoom}) translate(${zp.pan.x / zp.zoom}px, ${zp.pan.y / zp.zoom}px)`,
              transformOrigin: 'center center',
              transition: zp.isDragging ? 'none' : 'transform 0.18s ease-out',
              opacity: status === 'loaded' ? 1 : 0,
              imageRendering: zp.zoom > 4 ? 'pixelated' : 'auto',
              willChange: 'transform',
            }}
          />
        )}

        {/* Zoom + control bar — bottom-right overlay */}
        {status === 'loaded' && (
          <div
            className="absolute flex items-center gap-0.5"
            style={{
              bottom: 8, right: 8,
              background: 'rgba(0,0,0,0.58)',
              backdropFilter: 'blur(4px)',
              borderRadius: 4,
              padding: '2px 3px',
              gap: 1,
            }}
            onMouseDown={e => e.stopPropagation()}
          >
            {ctrlBtn('Rotate left', <RotateCcw style={{ width: 11, height: 11 }} />, zp.rotateLeft)}
            {ctrlBtn('Rotate right  (R)', <RotateCw style={{ width: 11, height: 11 }} />, zp.rotateRight)}
            <div style={{ width: 1, height: 12, background: 'rgba(255,255,255,0.18)', margin: '0 1px', flexShrink: 0 }} />
            {ctrlBtn('Zoom out  (−)', <ZoomOut style={{ width: 11, height: 11 }} />, zp.zoomOut)}
            <span
              onClick={e => { e.stopPropagation(); zp.reset() }}
              title="Reset all  (0)"
              style={{
                fontSize: 8,
                fontVariantNumeric: 'tabular-nums',
                color: 'rgba(255,255,255,0.7)',
                cursor: 'pointer',
                padding: '0 3px',
                minWidth: 30,
                textAlign: 'center',
              }}
            >
              {Math.round(zp.zoom * 100)}%
            </span>
            {ctrlBtn('Zoom in  (+)', <ZoomIn style={{ width: 11, height: 11 }} />, zp.zoomIn)}
            {onExpand && ctrlBtn('Open full view', <Maximize2 style={{ width: 10, height: 10 }} />, onExpand)}
            {autoKeyboard && ctrlBtn(
              fsActive ? 'Exit fullscreen  (F)' : 'Fullscreen  (F)',
              fsActive ? <Minimize2 style={{ width: 10, height: 10 }} /> : <Maximize2 style={{ width: 10, height: 10 }} />,
              toggleFs,
              fsActive,
            )}
          </div>
        )}

        {/* Expand hint — top-left when not zoomed */}
        {status === 'loaded' && onExpand && zp.zoom <= 1 && (
          <div
            className="absolute"
            style={{
              top: 6, left: 6,
              fontSize: 8,
              color: 'rgba(255,255,255,0.35)',
              pointerEvents: 'none',
            }}
          >
            Click to inspect
          </div>
        )}
      </div>

      {/* Metadata chips below viewport */}
      {showChips && (
        <LabelMetaChips source={source} dims={dims} labelPreview={labelPreview} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: lightbox — full-screen label inspection modal
// ---------------------------------------------------------------------------

function LabelLightbox({
  blobUrl, status, source, dims, setDims, labelPreview, onClose,
}: Omit<ViewerProps, 'containerH' | 'onExpand' | 'autoKeyboard'> & { onClose: () => void }) {
  // ESC → close
  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.fullscreenElement) onClose()
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [onClose])

  // Prevent body scroll
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  const metadata = labelPreview
  const stain    = metadata?.stain_matched ?? metadata?.stain_type
  const caseId   = metadata?.case_id
  const sugName  = metadata?.suggested_filename

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.88)' }}
      onClick={onClose}
    >
      <div
        style={{ width: 'min(820px, 94vw)', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-3 px-1">
          <div className="flex items-center gap-3">
            <p className="text-[9px] uppercase tracking-widest" style={{ color: 'rgba(255,255,255,0.4)' }}>
              Label Inspection
            </p>
            {caseId && (
              <span className="text-[10px] font-mono" style={{ color: 'rgba(255,255,255,0.65)' }}>{caseId}</span>
            )}
            {stain && (
              <span className="text-[10px] font-mono" style={{ color: 'var(--accent)' }}>{stain}</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[8px]" style={{ color: 'rgba(255,255,255,0.3)' }}>
              +/−  zoom  ·  R  rotate  ·  0  reset  ·  F  fullscreen  ·  Esc  close
            </span>
            <button
              type="button"
              onClick={onClose}
              style={{ color: 'rgba(255,255,255,0.5)', lineHeight: 1 }}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Viewer */}
        <LabelImageViewer
          blobUrl={blobUrl}
          status={status}
          source={source}
          dims={dims}
          setDims={setDims}
          containerH={620}
          labelPreview={labelPreview}
          showChips
          autoKeyboard
        />

        {/* Suggested filename strip */}
        {sugName && (
          <div
            className="mt-3 px-3 py-2 rounded"
            style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
          >
            <span className="text-[8px] uppercase tracking-wider" style={{ color: 'rgba(255,255,255,0.3)' }}>
              Suggested filename
            </span>
            <p className="font-mono text-[11px] mt-0.5" style={{ color: 'var(--accent)' }}>{sugName}</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: compact evidence strip (Correct Filename tab)
// ---------------------------------------------------------------------------

function CompactEvidenceStrip({ fileId }: { fileId: number }) {
  const BASE          = '/dashboard/api'
  const imageUrl      = `${BASE}/recovery/files/${fileId}/label-image`
  const [imgErr, setImgErr] = useState(false)

  const { data } = useQuery({
    queryKey: ['labelPreview', fileId],
    queryFn:  () => fetchLabelPreview(fileId),
    staleTime: 60_000,
  })

  const stain    = data?.stain_matched ?? data?.stain_type
  const caseId   = data?.case_id
  const scanner  = data?.scanner_id
  const sugName  = data?.suggested_filename

  return (
    <div
      className="rounded flex gap-2.5 items-start"
      style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)', padding: '7px 10px' }}
    >
      {/* Thumbnail */}
      {imgErr ? (
        <div
          className="flex-shrink-0 flex items-center justify-center rounded"
          style={{ width: 48, height: 46, background: 'var(--surface-0)', border: '1px dashed var(--border-faint)' }}
        >
          <ImageOff style={{ width: 14, height: 14, color: 'var(--text-faint)', opacity: 0.5 }} />
        </div>
      ) : (
        <img
          src={imageUrl}
          alt="Label thumbnail"
          onError={() => setImgErr(true)}
          style={{
            height: 46, width: 'auto', borderRadius: 3, flexShrink: 0,
            border: '1px solid var(--border-faint)', opacity: 0.9,
          }}
        />
      )}

      {/* Facts */}
      <div className="flex-1 min-w-0">
        <p className="text-[8px] uppercase tracking-widest mb-1.5" style={{ color: 'var(--text-faint)' }}>
          Label Evidence
        </p>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
          {caseId && (
            <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
              Case: <span className="font-mono" style={{ color: 'var(--accent)' }}>{caseId}</span>
            </span>
          )}
          {stain && (
            <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
              Stain: <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{stain}</span>
            </span>
          )}
          {scanner && (
            <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
              Scanner: <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{scanner}</span>
            </span>
          )}
          {sugName && (
            <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
              Suggested: <span className="font-mono" style={{ color: 'var(--accent)' }}>{sugName}</span>
            </span>
          )}
          {!caseId && !stain && !scanner && !sugName && (
            <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>No extraction data.</span>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: full label metadata panel (Inspect Label tab)
// ---------------------------------------------------------------------------

function LabelMetadataPanel({ fileId }: { fileId: number }) {
  const BASE = '/dashboard/api'

  const { data: labelPreview, isPending } = useQuery({
    queryKey: ['labelPreview', fileId],
    queryFn:  () => fetchLabelPreview(fileId),
    staleTime: 60_000,
  })

  const imageUrl = `${BASE}/recovery/files/${fileId}/label-image`
  const img      = useLabelImage(imageUrl)
  const [lightboxOpen, setLightboxOpen] = useState(false)

  if (isPending) {
    return (
      <div
        className="rounded-lg animate-pulse"
        style={{ height: 280, background: 'var(--surface-inset)' }}
      />
    )
  }

  const rows: Array<{ label: string; value: string | null | undefined; accent?: string }> = [
    { label: 'Slide ID',          value: labelPreview?.slide_id },
    { label: 'Case ID',           value: labelPreview?.case_id },
    { label: 'Scanner',           value: labelPreview?.scanner_id },
    { label: 'Vendor',            value: labelPreview?.scanner_vendor },
    { label: 'Stain (extracted)', value: labelPreview?.stain_matched ?? labelPreview?.stain_type },
    { label: 'Stain OCR raw',     value: labelPreview?.stain_ocr_raw },
    { label: 'DataMatrix',        value: labelPreview?.datamatrix_raw },
    { label: 'DM decode',         value: labelPreview?.datamatrix_decode_status },
    { label: 'DM error',          value: labelPreview?.datamatrix_error, accent: 'var(--chart-rose)' },
    { label: 'ROI case',          value: labelPreview?.roi_case_number },
    { label: 'ROI lab',           value: labelPreview?.roi_lab_id },
    { label: 'ROI stain',         value: labelPreview?.roi_stain },
    {
      label: 'Routed as',
      value: labelPreview?.routing_type,
      accent: labelPreview?.routing_type === 'failed' ? 'var(--chart-rose)' : 'var(--chart-amber)',
    },
    { label: 'Routing reason',    value: labelPreview?.routing_reason },
    { label: 'Suggested name',    value: labelPreview?.suggested_filename, accent: 'var(--accent)' },
  ].filter(r => r.value)

  return (
    <>
      <div
        className="rounded-lg overflow-hidden"
        style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
      >
        {/* Image viewer */}
        <div className="p-3" style={{ borderBottom: '1px solid var(--border-faint)' }}>
          <LabelImageViewer
            blobUrl={img.blobUrl}
            status={img.status}
            source={img.source}
            dims={img.dims}
            setDims={img.setDims}
            containerH={280}
            labelPreview={labelPreview}
            onExpand={() => setLightboxOpen(true)}
            showChips
          />
        </div>

        {/* Extraction metadata rows */}
        <div className="px-4 py-3 space-y-1">
          <p className="text-[9px] uppercase tracking-wider mb-2" style={{ color: 'var(--text-faint)' }}>
            Extraction Data
          </p>
          {rows.length === 0 ? (
            <p className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
              {labelPreview?.unavailable_reason
                ? `No data — ${labelPreview.unavailable_reason.replace(/_/g, ' ')}`
                : 'No extraction data found for this file.'}
            </p>
          ) : (
            rows.map(({ label, value, accent }) => (
              <div key={label} className="flex gap-2 text-[10px]">
                <span className="w-28 flex-shrink-0 font-medium" style={{ color: 'var(--text-faint)' }}>
                  {label}
                </span>
                <span className="font-mono break-all" style={{ color: accent ?? 'var(--text-secondary)' }}>
                  {value}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {lightboxOpen && (
        <LabelLightbox
          blobUrl={img.blobUrl}
          status={img.status}
          source={img.source}
          dims={img.dims}
          setDims={img.setDims}
          labelPreview={labelPreview}
          onClose={() => setLightboxOpen(false)}
        />
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: live filename validation panel
// ---------------------------------------------------------------------------

function ValidationPanel({
  filename,
  serverResult,
  isPending: validating,
  onApplyNormalized,
}: {
  filename: string
  serverResult: FilenameValidationResponse | null
  isPending: boolean
  onApplyNormalized?: (name: string) => void
}) {
  if (!filename.trim()) return null

  if (validating) {
    return (
      <div className="text-[10px] animate-pulse" style={{ color: 'var(--text-faint)' }}>
        Validating…
      </div>
    )
  }

  if (!serverResult) return null

  const cls   = serverResult.classification
  const color =
    cls === 'valid'           ? 'var(--chart-teal)'  :
    cls === 'partially_valid' ? 'var(--chart-amber)' :
                                'var(--chart-rose)'
  const Icon  =
    cls === 'valid'           ? CheckCircle :
    cls === 'partially_valid' ? Clock       :
                                XCircle

  return (
    <div
      className="rounded-lg px-3 py-2 space-y-1.5"
      style={{ background: 'var(--surface-inset)', border: `1px solid ${color}22` }}
    >
      <div className="flex items-center gap-1.5">
        <Icon className="h-3 w-3 flex-shrink-0" style={{ color }} aria-hidden />
        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color }}>
          {cls === 'valid' ? 'Valid' : cls === 'partially_valid' ? 'Partially valid' : 'Invalid'}
        </span>
      </div>

      {serverResult.components && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
          {[
            { label: 'Case ID',   value: serverResult.components.case_id },
            { label: 'Pot',       value: serverResult.components.pot },
            { label: 'Block',     value: serverResult.components.block },
            { label: 'Section',   value: serverResult.components.section },
            { label: 'Stain',     value: serverResult.components.stain },
            { label: 'Timestamp', value: serverResult.components.timestamp ?? '(missing — will extract)' },
          ].map(({ label, value }) => value ? (
            <div key={label} className="flex gap-1 text-[9px]">
              <span style={{ color: 'var(--text-faint)' }}>{label}</span>
              <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{value}</span>
            </div>
          ) : null)}
        </div>
      )}

      {serverResult.errors.map(e => (
        <p key={e.code} className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>{e.message}</p>
      ))}
      {serverResult.warnings.map(w => (
        <p key={w.code} className="text-[10px]" style={{ color: 'var(--chart-amber)' }}>{w.message}</p>
      ))}
      {serverResult.suggested_correction && cls !== 'valid' && (
        <p className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
          Suggestion:{' '}
          <span className="font-mono" style={{ color: 'var(--accent)' }}>
            {serverResult.suggested_correction}
          </span>
        </p>
      )}

      {serverResult.normalized_filename &&
        serverResult.normalized_filename !== filename &&
        onApplyNormalized && (
        <div
          className="flex items-center justify-between gap-2 rounded px-2 py-1.5"
          style={{ background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.20)' }}
        >
          <span className="text-[9px] font-mono truncate" style={{ color: 'var(--accent)' }}>
            {serverResult.normalized_filename}
          </span>
          <button
            type="button"
            onClick={() => onApplyNormalized(serverResult.normalized_filename!)}
            className="flex-shrink-0 text-[9px] font-semibold px-2 py-0.5 rounded"
            style={{ background: 'var(--accent)', color: 'var(--surface-1)', whiteSpace: 'nowrap' }}
          >
            Apply canonical
          </button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Client-side stain synonym normalization
// ---------------------------------------------------------------------------

const _STAIN_SYNONYMS: Record<string, string> = {
  he: 'H&E', 'h-e': 'H&E', 'h+e': 'H&E',
  hematoxylin: 'H&E', haematoxylin: 'H&E',
  'pas-d': 'PAS', pasd: 'PAS',
  masson: 'MT', massons: 'MT', 'masson-trichrome': 'MT',
  zn: 'Ziehl', 'ziehl-neelsen': 'Ziehl', ziehlneelsen: 'Ziehl',
  ki67: 'KI-67', grocotts: 'Grocott', 'grocott-methenamine': 'Grocott',
}

function canonicalStain(raw: string): string {
  return _STAIN_SYNONYMS[raw.toLowerCase()] ?? raw
}

// ---------------------------------------------------------------------------
// Sub-component: structured rename builder
// ---------------------------------------------------------------------------

interface BuilderState {
  prefix: string; year: string; caseNum: string
  pot: string; block: string; section: string
  stain: string; extension: string
  addTimestamp: boolean; tsDate: string; tsTime: string
}

function assembleName(s: BuilderState): string {
  const paddedCase = s.caseNum.replace(/\D/g, '').padStart(6, '0')
  const base = `${s.prefix}${s.year}${paddedCase}${s.pot}-${s.block}-${s.section}-${s.stain}`
  if (!base.includes('-')) return `${base}${s.extension}`
  if (s.addTimestamp && s.tsDate && s.tsTime) {
    const [hh = '00', mm = '00', ss = '00'] = s.tsTime.split(':')
    return `${base}_UTC${s.tsDate}T${hh}_${mm}_${ss}Z${s.extension}`
  }
  return `${base}${s.extension}`
}

function parseCaseId(caseId: string | null | undefined) {
  if (!caseId) return null
  const m = /^([A-Z])(\d{4})(\d{6})$/.exec(caseId)
  return m ? { prefix: m[1], year: m[2], caseNum: m[3] } : null
}

function todayDate() { return new Date().toISOString().slice(0, 10) }

function StructuredBuilderForm({
  file, onFilenameChange,
}: { file: MonitoredFileItem; onFilenameChange: (f: string) => void }) {
  const { data: lp } = useQuery<LabelPreviewResponse>({
    queryKey: ['labelPreview', file.file_id],
    queryFn:  () => fetchLabelPreview(file.file_id),
    staleTime: 60_000,
  })
  const parsed = parseCaseId(lp?.case_id ?? file.case_id)

  const [state, setState] = useState<BuilderState>(() => ({
    prefix:       parsed?.prefix ?? 'N',
    year:         parsed?.year   ?? String(new Date().getFullYear()),
    caseNum:      parsed?.caseNum ?? '',
    pot:          'SA', block: '1', section: '1',
    stain:        lp?.stain_matched ?? lp?.stain_type ?? '',
    extension:    file.extension ?? (file.filename.includes('.') ? `.${file.filename.split('.').pop()}` : '.svs'),
    addTimestamp: false, tsDate: todayDate(), tsTime: '00:00:00',
  }))

  const populated = useRef(false)
  useEffect(() => {
    if (populated.current || !lp) return
    const fp = parseCaseId(lp.case_id ?? file.case_id)
    setState(p => ({
      ...p,
      ...(fp ? { prefix: fp.prefix, year: fp.year, caseNum: fp.caseNum } : {}),
      stain: lp.stain_matched ?? lp.stain_type ?? p.stain,
    }))
    populated.current = true
  }, [lp, file.case_id])

  const set = (k: keyof BuilderState, v: string | boolean) => setState(p => ({ ...p, [k]: v }))

  useEffect(() => {
    const a = assembleName(state)
    if (a && a !== state.extension) onFilenameChange(a)
  }, [state]) // eslint-disable-line

  const paddedPreview = state.caseNum.replace(/\D/g, '').padStart(6, '0')
  const caseIdPreview = `${state.prefix}${state.year}${paddedPreview}`
  const assembled     = assembleName(state)

  const inp = "rounded px-2.5 py-1.5 text-[11px] font-mono w-full outline-none"
  const inpS = { background: 'var(--surface-inset)', border: '1px solid var(--border-default)', color: 'var(--text-primary)' } as const
  const lbl  = "text-[9px] uppercase tracking-wider block mb-1"
  const lblS = { color: 'var(--text-faint)' } as const

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <div><label className={lbl} style={lblS}>Prefix</label>
          <input className={inp} style={inpS} value={state.prefix} maxLength={2} placeholder="N"
            onChange={e => set('prefix', e.target.value.toUpperCase())} /></div>
        <div><label className={lbl} style={lblS}>Year</label>
          <input className={inp} style={inpS} value={state.year} maxLength={4} placeholder="2024"
            onChange={e => set('year', e.target.value.replace(/\D/g, ''))} /></div>
        <div><label className={lbl} style={lblS}>Case № (auto-pads)</label>
          <input className={inp} style={inpS} value={state.caseNum} maxLength={6} placeholder="002863"
            onChange={e => set('caseNum', e.target.value.replace(/\D/g, ''))} /></div>
      </div>

      {caseIdPreview.length > 1 && (
        <div className="flex items-center gap-1.5">
          <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>Case ID →</span>
          <span className="font-mono text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: 'var(--accent-faint)', color: 'var(--accent)' }}>{caseIdPreview}</span>
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <div><label className={lbl} style={lblS}>Pot</label>
          <input className={inp} style={inpS} value={state.pot} maxLength={4} placeholder="SA"
            onChange={e => set('pot', e.target.value.toUpperCase())} /></div>
        <div><label className={lbl} style={lblS}>Block</label>
          <input className={inp} style={inpS} value={state.block} maxLength={3} placeholder="1"
            onChange={e => set('block', e.target.value.replace(/\D/g, ''))} /></div>
        <div><label className={lbl} style={lblS}>Section / Slide</label>
          <input className={inp} style={inpS} value={state.section} maxLength={3} placeholder="1"
            onChange={e => set('section', e.target.value.replace(/\D/g, ''))} /></div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className={lbl} style={lblS}>Stain</label>
          <input
            className={inp} style={inpS}
            list="pathoryx-stain-list" value={state.stain}
            placeholder="H&E"
            onChange={e => set('stain', e.target.value)}
            onBlur={e => {
              const canon = canonicalStain(e.target.value)
              if (canon !== e.target.value) set('stain', canon)
            }}
          />
          <datalist id="pathoryx-stain-list">
            {STAIN_LIST.map(s => <option key={s} value={s} />)}
          </datalist>
        </div>
        <div>
          <label className={lbl} style={lblS}>
            Extension
            {state.extension === (file.extension ?? '.svs') && (
              <span className="ml-1 text-[8px]" style={{ color: 'var(--text-faint)' }}>
                (original WSI type)
              </span>
            )}
          </label>
          <select
            className={inp}
            style={{ ...inpS, cursor: 'pointer' }}
            value={state.extension}
            onChange={e => set('extension', e.target.value)}
          >
            {SUPPORTED_EXTENSIONS.map(x => <option key={x} value={x}>{x}</option>)}
          </select>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button type="button" onClick={() => set('addTimestamp', !state.addTimestamp)}
          className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
          <span className="inline-flex items-center justify-center rounded-full flex-shrink-0"
            style={{ width: 14, height: 14,
              border: `2px solid ${state.addTimestamp ? 'var(--accent)' : 'var(--border-default)'}`,
              background: state.addTimestamp ? 'var(--accent)' : 'transparent' }} />
          Include UTC scan timestamp
        </button>
      </div>

      {state.addTimestamp && (
        <div className="grid grid-cols-2 gap-2 pl-4">
          <div><label className={lbl} style={lblS}>Date (yyyy-MM-dd)</label>
            <input type="date" className={inp} style={inpS} value={state.tsDate}
              onChange={e => set('tsDate', e.target.value)} /></div>
          <div><label className={lbl} style={lblS}>Time (HH:mm:ss)</label>
            <input type="time" step="1" className={inp} style={inpS} value={state.tsTime}
              onChange={e => set('tsTime', e.target.value)} /></div>
        </div>
      )}

      <div className="rounded px-3 py-2"
        style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}>
        <p className="text-[9px] uppercase tracking-wider mb-1" style={{ color: 'var(--text-faint)' }}>
          Assembled filename
        </p>
        <p className="font-mono text-[11px] break-all"
          style={{ color: assembled === state.extension ? 'var(--text-faint)' : 'var(--text-primary)' }}>
          {assembled || <span style={{ color: 'var(--text-faint)' }}>Fill in the fields above</span>}
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: audit trail timeline
// ---------------------------------------------------------------------------

const CHANGE_TYPE_LABEL: Record<string, string> = {
  rename: 'renamed', replace: 'replaced', move: 'moved',
  new_file: 'first seen', removed: 'removed', checksum_change: 'content changed',
  size_change: 'size changed', metadata_update: 'metadata updated',
}

function AuditTimeline({ fileId }: { fileId: number }) {
  const { data, isPending } = useQuery({
    queryKey: ['auditTrail', fileId],
    queryFn:  () => fetchAuditTrail(fileId),
  })

  if (isPending) return <p className="text-[10px] animate-pulse" style={{ color: 'var(--text-faint)' }}>Loading history…</p>

  const changes: AuditChangeItem[] = data?.changes ?? []
  const events:  AuditEventItem[]  = data?.events  ?? []

  type TLItem =
    | { kind: 'change'; ts: string | null; item: AuditChangeItem }
    | { kind: 'event';  ts: string | null; item: AuditEventItem }

  const items: TLItem[] = [
    ...changes.map(c => ({ kind: 'change' as const, ts: c.detected_at, item: c })),
    ...events.map(e => ({ kind: 'event' as const,  ts: e.occurred_at,  item: e })),
  ].sort((a, b) => (a.ts ?? '').localeCompare(b.ts ?? ''))

  if (items.length === 0) return (
    <p className="text-[10px]" style={{ color: 'var(--text-faint)' }}>No audit records found for this file.</p>
  )

  return (
    <div className="space-y-2">
      {items.map(entry => {
        if (entry.kind === 'change') {
          const c = entry.item as AuditChangeItem
          const src = c.inferred_action === 'dashboard_correction' ? 'dashboard' : 'filesystem'
          return (
            <div key={`c-${c.change_id}`} className="flex gap-2 text-[10px]">
              <span className="text-[9px] font-mono tabular mt-0.5 flex-shrink-0 w-16 text-right"
                style={{ color: 'var(--text-faint)' }} title={fmtDatetime(c.detected_at)}>
                {fmtRelative(c.detected_at)}
              </span>
              <div className="flex-1 min-w-0">
                <span style={{ color: 'var(--text-secondary)' }}>
                  {CHANGE_TYPE_LABEL[c.change_type] ?? c.change_type.replace(/_/g, ' ')}
                </span>
                {c.new_filename && c.old_filename && c.new_filename !== c.old_filename && (
                  <span style={{ color: 'var(--text-faint)' }}>
                    {' '}{c.old_filename} → <span className="font-mono" style={{ color: 'var(--accent)' }}>{c.new_filename}</span>
                  </span>
                )}
                <span className="ml-1" style={{ color: 'var(--text-faint)' }}>via {src}</span>
                {c.review_status && (
                  <span className="ml-1.5 px-1 py-0.5 rounded text-[9px] font-medium"
                    style={{ color: REVIEW_STATE_COLOR[c.review_status] ?? 'var(--text-faint)', background: 'var(--accent-faint)' }}>
                    {REVIEW_STATE_LABELS[c.review_status] ?? c.review_status}
                  </span>
                )}
                {c.technician_notes && (
                  <p className="mt-0.5 italic" style={{ color: 'var(--text-faint)' }}>"{c.technician_notes}"</p>
                )}
              </div>
            </div>
          )
        } else {
          const e = entry.item as AuditEventItem
          const isReview = e.event_type.startsWith('dashboard.')
          return (
            <div key={`e-${e.event_id}`} className="flex gap-2 text-[10px]">
              <span className="text-[9px] font-mono tabular mt-0.5 flex-shrink-0 w-16 text-right"
                style={{ color: 'var(--text-faint)' }} title={fmtDatetime(e.occurred_at)}>
                {fmtRelative(e.occurred_at)}
              </span>
              <span className={isReview ? 'font-medium' : ''}
                style={{ color: isReview ? 'var(--accent)' : 'var(--text-faint)' }}>
                {fmtEventType(e.event_type)}
              </span>
            </div>
          )
        }
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: confirm dialog
// ---------------------------------------------------------------------------

function ConfirmRenameDialog({
  filename, onConfirm, onCancel, isPending,
}: { filename: string; onConfirm: () => void; onCancel: () => void; isPending: boolean }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.60)' }}>
      <div className="rounded-xl p-5 w-[440px] max-w-[94vw]"
        style={{ background: 'var(--surface-1)', border: '1px solid var(--border-default)' }}>
        <p className="text-[9px] uppercase tracking-[0.15em] mb-2" style={{ color: 'var(--text-faint)' }}>
          Confirm Recovery Action
        </p>
        <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
          Apply filename correction
        </p>
        <p className="text-[11px] leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>
          This will rename and move the file, update the artifact record, and requeue QC.
          The original filename is preserved in the audit history.
        </p>
        <div className="rounded px-3 py-2 mb-4 font-mono text-[10px] break-all"
          style={{ background: 'var(--surface-inset)', color: 'var(--accent)', border: '1px solid var(--border-faint)' }}>
          {filename}
        </div>
        <div className="flex gap-3 justify-end">
          <button type="button" onClick={onCancel} disabled={isPending}
            className="px-4 py-1.5 rounded text-[11px]"
            style={{ color: 'var(--text-muted)', border: '1px solid var(--border-default)' }}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm} disabled={isPending}
            className="px-4 py-1.5 rounded text-[11px] font-semibold"
            style={{ color: 'var(--surface-1)', background: 'var(--accent)', opacity: isPending ? 0.6 : 1 }}>
            {isPending ? 'Applying…' : 'Apply Recovery'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main drawer
// ---------------------------------------------------------------------------

type DrawerTab  = 'inspect' | 'rename' | 'history'
type RenameMode = 'quick' | 'builder'

interface Props {
  file: MonitoredFileItem
  onClose: () => void
}

export function TechnicianReviewDrawer({ file, onClose }: Props) {
  const queryClient = useQueryClient()

  const [proposedFilename, setProposedFilename] = useState(file.filename)
  const [note, setNote]                         = useState('')
  const [confirming, setConfirming]             = useState(false)
  const [renameResult, setRenameResult]         = useState<TechnicianRenameResponse | null>(null)
  const [tab, setTab]                           = useState<DrawerTab>('inspect')
  const [renameMode, setRenameMode]             = useState<RenameMode>('quick')
  const inputRef = useRef<HTMLInputElement>(null)

  const [validationResult, setValidationResult] = useState<FilenameValidationResponse | null>(null)
  const [validating, setValidating]             = useState(false)

  useEffect(() => {
    const proposed = proposedFilename.trim()
    if (!proposed) { setValidationResult(null); return }
    const timer = setTimeout(async () => {
      setValidating(true)
      try { setValidationResult(await postValidateFilename(proposed, file.extension ?? undefined)) }
      catch { setValidationResult(null) }
      finally { setValidating(false) }
    }, 250)
    return () => clearTimeout(timer)
  }, [proposedFilename])

  const renameMutation = useMutation({
    mutationFn: () => postTechnicianRename(file.file_id, {
      proposed_filename: proposedFilename.trim(),
      technician_note:   note.trim() || undefined,
      confirm: true,
    }),
    onSuccess: data => {
      setRenameResult(data); setConfirming(false)
      queryClient.invalidateQueries({ queryKey: ['monitoredFiles'] })
      queryClient.invalidateQueries({ queryKey: ['watchFolders'] })
      queryClient.invalidateQueries({ queryKey: ['recovery'] })
      queryClient.invalidateQueries({ queryKey: ['failures'] })
      queryClient.invalidateQueries({ queryKey: ['auditTrail', file.file_id] })
    },
    onError: () => setConfirming(false),
  })

  const reviewMutation = useMutation({
    mutationFn: ({ status, note: n }: { status: string; note?: string }) => {
      if (!file.change_id) throw new Error('No TechnicianChange linked to this file')
      return patchReviewState(file.change_id, { review_status: status, technician_note: n })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['monitoredFiles'] })
      queryClient.invalidateQueries({ queryKey: ['auditTrail', file.file_id] })
    },
  })

  const canRename =
    validationResult !== null &&
    validationResult.classification !== 'invalid' &&
    proposedFilename.trim() !== ''

  const { data: labelPreview } = useQuery<LabelPreviewResponse>({
    queryKey: ['labelPreview', file.file_id],
    queryFn:  () => fetchLabelPreview(file.file_id),
    staleTime: 60_000,
  })

  const folderDisplay: Record<string, string> = {
    failed: 'Failed', suspicious: 'Suspicious', manual_review: 'Manual Review',
  }

  const workflowSource =
    file.inferred_action === 'dashboard_correction' ? 'Dashboard correction' :
    file.change_type === 'rename'                   ? 'Manual folder rename'  :
    file.change_type === 'new_file'                 ? 'New file detected'     :
    file.change_type                                ? `Detected: ${file.change_type.replace(/_/g, ' ')}` :
                                                      'Awaiting technician'

  const reviewStatus = file.review_status ?? null

  return (
    <>
      <div className="fixed inset-0 z-40" style={{ background: 'rgba(0,0,0,0.35)' }}
        onClick={onClose} aria-hidden />

      <div
        className="fixed top-0 right-0 h-full z-50 flex flex-col"
        style={{
          width: 'min(560px, 96vw)',
          background: 'var(--surface-1)',
          borderLeft: '1px solid var(--border-default)',
          boxShadow: '-8px 0 40px rgba(0,0,0,0.45)',
        }}
      >
        {/* Header */}
        <div className="flex items-start gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border-faint)' }}>
          <div className="flex-1 min-w-0">
            <p className="text-[9px] font-semibold uppercase tracking-[0.15em]" style={{ color: 'var(--text-faint)' }}>
              Technician Review — {folderDisplay[file.folder_label] ?? file.folder_label}
            </p>
            <p className="text-xs font-medium truncate mt-0.5" style={{ color: 'var(--text-primary)' }}
              title={file.filename}>{file.filename}</p>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1">
              {file.case_id && (
                <span className="text-[9px] font-mono" style={{ color: 'var(--accent)' }}>{file.case_id}</span>
              )}
              {file.file_size != null && (
                <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>{fmtBytes(file.file_size)}</span>
              )}
              <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>{workflowSource}</span>
              {reviewStatus && (
                <span className="text-[9px] font-medium px-1.5 py-0.5 rounded"
                  style={{ color: REVIEW_STATE_COLOR[reviewStatus] ?? 'var(--text-faint)', background: 'var(--accent-faint)' }}>
                  {REVIEW_STATE_LABELS[reviewStatus] ?? reviewStatus}
                </span>
              )}
            </div>
          </div>
          <button type="button" onClick={onClose} className="flex-shrink-0 p-1 rounded mt-0.5"
            style={{ color: 'var(--text-faint)' }} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Review state actions */}
        {file.change_id && !renameResult && (
          <div className="flex items-center gap-2 px-5 py-2.5 flex-shrink-0"
            style={{ borderBottom: '1px solid var(--border-faint)', background: 'var(--accent-faint)' }}>
            <span className="text-[9px] uppercase tracking-wider mr-1" style={{ color: 'var(--text-faint)' }}>
              Mark as
            </span>
            {([
              { status: 'investigating', label: 'Investigating' },
              { status: 'dismissed',     label: 'Dismiss' },
            ] as const).map(({ status, label }) => {
              const current  = file.review_status
              const isActive = current === status
              const blocked  = reviewMutation.isPending || isActive ||
                (status === 'dismissed'     && current === 'dismissed') ||
                (status === 'investigating' && current === 'investigating')
              return (
                <button key={status} type="button" disabled={blocked}
                  onClick={() => reviewMutation.mutate({ status, note: undefined })}
                  className="px-2.5 py-0.5 rounded text-[10px] font-medium"
                  style={{
                    color:      isActive ? 'var(--surface-1)' : REVIEW_STATE_COLOR[status],
                    background: isActive ? REVIEW_STATE_COLOR[status] : 'transparent',
                    border:     `1px solid ${REVIEW_STATE_COLOR[status]}44`,
                    opacity:    blocked ? 0.4 : 1,
                    cursor:     blocked ? 'not-allowed' : 'pointer',
                  }}>
                  {label}
                </button>
              )
            })}
            {reviewMutation.isError && (
              <span className="text-[9px] ml-1" style={{ color: 'var(--chart-rose)' }}>
                {(reviewMutation.error as Error)?.message ?? 'Update failed'}
              </span>
            )}
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-0 flex-shrink-0" style={{ borderBottom: '1px solid var(--border-faint)' }}>
          {([
            { id: 'inspect' as const,  label: 'Inspect Label' },
            { id: 'rename'  as const,  label: 'Correct Filename' },
            { id: 'history' as const,  label: 'Audit Trail' },
          ]).map(({ id, label }) => (
            <button key={id} type="button"
              onClick={() => { setTab(id); if (id === 'rename') setTimeout(() => inputRef.current?.focus(), 50) }}
              className="px-4 py-2.5 text-[10px] font-medium tracking-wide"
              style={{
                color:        tab === id ? 'var(--accent)' : 'var(--text-muted)',
                borderBottom: tab === id ? '2px solid var(--accent)' : '2px solid transparent',
                background:   'transparent',
              }}>
              {label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">

          {/* ── INSPECT ── */}
          {tab === 'inspect' && (
            <div className="space-y-4">
              {file.recovery_reason && (
                <div className="rounded px-3 py-2 flex items-start gap-2 text-[10px]"
                  style={{ background: 'rgba(217,119,6,0.06)', border: '1px solid rgba(217,119,6,0.20)' }}>
                  <span className="font-semibold flex-shrink-0" style={{ color: 'var(--chart-amber)' }}>
                    Failure reason
                  </span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {file.recovery_reason.replace(/_/g, ' ')}
                  </span>
                </div>
              )}
              <LabelMetadataPanel fileId={file.file_id} />
            </div>
          )}

          {/* ── CORRECT FILENAME ── */}
          {tab === 'rename' && (
            <div className="space-y-4">
              {renameResult ? (
                <div className="rounded-lg px-4 py-3 space-y-2"
                  style={{
                    background: renameResult.outcome === 'auto_recovered'
                      ? 'rgba(52,211,153,0.06)' : 'rgba(217,119,6,0.06)',
                    border: `1px solid ${renameResult.outcome === 'auto_recovered'
                      ? 'rgba(52,211,153,0.20)' : 'rgba(217,119,6,0.20)'}`,
                  }}>
                  <p className="text-xs font-semibold"
                    style={{
                      color: renameResult.outcome === 'auto_recovered'
                        ? 'var(--chart-teal)'
                        : renameResult.outcome === 'validation_failed'
                        ? 'var(--chart-rose)'
                        : 'var(--chart-amber)'
                    }}>
                    {renameResult.outcome === 'auto_recovered'
                      ? 'File recovered — QC requeued'
                      : renameResult.outcome === 'manual_review_required'
                      ? 'Manual review still required'
                      : renameResult.outcome === 'validation_failed'
                      ? 'Rename rejected — filename invalid'
                      : renameResult.outcome}
                  </p>
                  {renameResult.reason && (
                    <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {renameResult.reason.replace(/_/g, ' ')}
                    </p>
                  )}
                  {renameResult.destination_path && (
                    <p className="text-[10px] font-mono break-all" style={{ color: 'var(--text-muted)' }}>
                      → {renameResult.destination_path}
                    </p>
                  )}
                  {renameResult.validation_error && (
                    <p className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>
                      {renameResult.validation_error}
                    </p>
                  )}
                </div>
              ) : (
                <>
                  <CompactEvidenceStrip fileId={file.file_id} />

                  {/* Mode toggle */}
                  <div className="flex rounded-md overflow-hidden flex-shrink-0 self-start"
                    style={{ border: '1px solid var(--border-default)' }}>
                    {([
                      { id: 'quick'   as const, label: 'Quick Rename' },
                      { id: 'builder' as const, label: 'Structured Builder' },
                    ]).map(({ id, label }) => (
                      <button key={id} type="button" onClick={() => setRenameMode(id)}
                        className="px-3 py-1.5 text-[10px] font-medium"
                        style={{
                          background: renameMode === id ? 'var(--accent)' : 'transparent',
                          color:      renameMode === id ? 'var(--surface-1)' : 'var(--text-muted)',
                          borderRight: id === 'quick' ? '1px solid var(--border-default)' : 'none',
                        }}>
                        {label}
                      </button>
                    ))}
                  </div>

                  {/* Quick Rename */}
                  {renameMode === 'quick' && (
                    <div className="space-y-3">
                      <div>
                        <label htmlFor="proposed-filename"
                          className="block text-[9px] uppercase tracking-wider mb-1.5"
                          style={{ color: 'var(--text-faint)' }}>
                          Proposed filename
                        </label>
                        <input id="proposed-filename" ref={inputRef} type="text"
                          value={proposedFilename} onChange={e => setProposedFilename(e.target.value)}
                          spellCheck={false} className="w-full rounded px-3 py-2 text-[11px] font-mono"
                          style={{
                            background: 'var(--surface-inset)',
                            border: `1px solid ${validationResult?.classification === 'invalid' ? 'rgba(225,29,72,0.40)' : 'var(--border-default)'}`,
                            color: 'var(--text-primary)', outline: 'none',
                          }}
                          placeholder="N2024002863SA-1-1-H&E.svs" />
                        {labelPreview?.suggested_filename &&
                          labelPreview.suggested_filename !== proposedFilename && (
                          <button type="button"
                            onClick={() => setProposedFilename(labelPreview.suggested_filename!)}
                            className="mt-1 text-[9px]" style={{ color: 'var(--accent)' }}>
                            ↑ Use suggested: {labelPreview.suggested_filename}
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Structured Builder */}
                  {renameMode === 'builder' && (
                    <StructuredBuilderForm file={file} onFilenameChange={setProposedFilename} />
                  )}

                  <ValidationPanel
                    filename={proposedFilename}
                    serverResult={validationResult}
                    isPending={validating}
                    onApplyNormalized={name => setProposedFilename(name)}
                  />

                  {canRename && validationResult?.components?.case_id && (
                    <div className="rounded px-3 py-2 text-[10px] font-mono"
                      style={{ background: 'var(--accent-faint)', border: '1px solid var(--border-default)' }}>
                      <span style={{ color: 'var(--text-faint)' }}>Destination → </span>
                      <span style={{ color: 'var(--accent)' }}>
                        final/{validationResult.components.case_id}/{proposedFilename.trim()}
                      </span>
                    </div>
                  )}

                  <div>
                    <label htmlFor="tech-note" className="block text-[9px] uppercase tracking-wider mb-1"
                      style={{ color: 'var(--text-faint)' }}>
                      Technician note (optional)
                    </label>
                    <textarea id="tech-note" value={note} onChange={e => setNote(e.target.value)}
                      rows={2} className="w-full rounded px-3 py-2 text-[11px] resize-none"
                      style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-default)',
                        color: 'var(--text-secondary)', outline: 'none' }}
                      placeholder="Corrected from OCR label reading…" />
                  </div>

                  {renameMutation.isError && (
                    <p className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>
                      Server error — rename could not be applied. Check audit trail.
                    </p>
                  )}

                  <button type="button" disabled={!canRename || renameMutation.isPending}
                    onClick={() => setConfirming(true)}
                    className="w-full py-2 rounded text-xs font-semibold flex items-center justify-center gap-2"
                    style={{
                      background: canRename ? 'var(--accent)' : 'var(--surface-inset)',
                      color:      canRename ? 'var(--surface-1)' : 'var(--text-faint)',
                      border:     canRename ? 'none' : '1px solid var(--border-default)',
                      cursor:     canRename ? 'pointer' : 'not-allowed',
                    }}>
                    {renameMode === 'builder' ? 'Apply Structured Rename' : 'Correct Filename'}
                    <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </>
              )}
            </div>
          )}

          {/* ── HISTORY ── */}
          {tab === 'history' && (
            <div className="space-y-3">
              <p className="section-label">Recovery & Review History</p>
              <AuditTimeline fileId={file.file_id} />
            </div>
          )}
        </div>
      </div>

      {confirming && (
        <ConfirmRenameDialog
          filename={proposedFilename.trim()}
          onConfirm={() => renameMutation.mutate()}
          onCancel={() => setConfirming(false)}
          isPending={renameMutation.isPending}
        />
      )}
    </>
  )
}
