// Use relative paths by default so:
//   dev:  Vite proxy at /dashboard/api/* forwards to http://127.0.0.1:8090
//   prod: same-origin requests go directly to FastAPI at /dashboard/api/*
// Set VITE_API_BASE_URL to an absolute URL only for cross-origin deployments.
const BASE = import.meta.env.VITE_API_BASE_URL
  ? `${import.meta.env.VITE_API_BASE_URL}/dashboard/api`
  : '/dashboard/api'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function makeUrl(path: string): URL {
  const full = `${BASE}${path.startsWith('/') ? path : `/${path}`}`
  // new URL() requires an absolute URL; supply window.location.origin as base
  // when BASE is a relative path (the common dev/prod case).
  return full.startsWith('http')
    ? new URL(full)
    : new URL(full, window.location.origin)
}

export async function apiPatch<T>(
  path: string,
  body: unknown,
): Promise<T> {
  const url = makeUrl(path)

  const res = await fetch(url.toString(), {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new ApiError(res.status, text || `HTTP ${res.status}`)
  }

  return res.json() as Promise<T>
}

export async function apiPost<T>(
  path: string,
  body: unknown,
): Promise<T> {
  const url = makeUrl(path)

  const res = await fetch(url.toString(), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new ApiError(res.status, text || `HTTP ${res.status}`)
  }

  return res.json() as Promise<T>
}

export async function apiFetch<T>(
  path: string,
  params?: Record<string, string | number>,
): Promise<T> {
  const url = makeUrl(path)

  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, String(v))
      }
    })
  }

  const res = await fetch(url.toString(), {
    headers: {
      Accept: 'application/json',
    },
  })

  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `HTTP ${res.status}`)
  }

  return res.json() as Promise<T>
}
