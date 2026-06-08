# Palantir Dashboard

Enterprise operations dashboard for the Palantir WSI pipeline.

- **Phase 1** — Read-only FastAPI backend API (port 8090)
- **Phase 2** — React + TypeScript + TailwindCSS frontend (port 5173 dev / served as static in prod)

---

## Quick start (both tiers)

### Step 1 — Start the backend API

```bash
# Install backend with dashboard extras
pip install -e ".[dashboard]"

# Set DATABASE_URL (required)
export DATABASE_URL=postgresql://pathoryx:secret@localhost:5432/pathoryx

# Start the FastAPI backend
pathoryx-dashboard
# → http://127.0.0.1:8090
# → Swagger UI: http://127.0.0.1:8090/dashboard/docs
```

### Step 2 — Start the frontend (development)

```bash
cd dashboard-ui
npm install
npm run dev
# → http://localhost:5173
```

The Vite dev server proxies all `/dashboard/api/*` requests to the backend at `127.0.0.1:8090`.

---

## Frontend build (production)

```bash
cd dashboard-ui
npm run build
# Output: dashboard-ui/dist/
```

Serve `dashboard-ui/dist/` with nginx (or any static file server) at the same host as the backend, or proxy all routes through nginx to the FastAPI app.

---

## Frontend pages & routes

| Route                      | Page              | Description                                           |
|----------------------------|-------------------|-------------------------------------------------------|
| `/`                        | Overview          | KPI cards, pipeline funnel, service health, activity  |
| `/slides`                  | Slide Explorer    | Searchable paginated table of all slides              |
| `/slides/:artifactId`      | Slide Detail      | Pipeline timeline, QC/DICOM/Upload detail, events     |
| `/queues`                  | Queue Monitor     | Queue depth per service, charts, trigger summary      |
| `/failures`                | Failure Center    | Failed slides + expandable failed trigger errors      |
| `/recovery`                | Recovery Center   | RecoverySentry technician change tracking             |

---

## Frontend architecture

```
dashboard-ui/
├── index.html
├── package.json
├── vite.config.ts        # Dev proxy + code splitting
├── tailwind.config.js    # Brand colors, card/button utilities
├── src/
│   ├── types/api.ts      # TypeScript types matching backend Pydantic schemas
│   ├── api/              # Thin fetch wrappers (one file per domain)
│   │   ├── client.ts     # Base apiFetch() with error handling
│   │   ├── overview.ts   ├── slides.ts   ├── events.ts
│   │   ├── queues.ts     ├── recovery.ts ├── failures.ts
│   │   └── services.ts
│   ├── hooks/            # React Query hooks (auto-polling, stale time)
│   │   ├── useOverview.ts   useSlides.ts   useSlideDetail.ts
│   │   ├── useEvents.ts     useQueues.ts   useRecovery.ts
│   │   └── useFailures.ts   useServicesHealth.ts
│   ├── utils/
│   │   ├── formatters.ts  # Date, bytes, duration, service name formatters
│   │   └── colors.ts      # Status → badge variant + Recharts hex palette
│   ├── components/
│   │   ├── layout/        # Shell, Sidebar, TopBar, ThemeProvider
│   │   ├── ui/            # KpiCard, StatusBadge, EmptyState, ErrorBanner, PageHeader, Spinners
│   │   └── charts/        # QueueBarChart, StatusDonut, ServiceQueueMiniChart
│   └── pages/             # Overview, SlideExplorer, SlideDetail,
│                          # QueueMonitor, FailureCenter, RecoveryCenter
```

### How frontend talks to backend

- All API calls go to `/dashboard/api/...` (same-origin URL)
- In dev: Vite proxies these to `http://127.0.0.1:8090` (no CORS needed)
- In prod: serve both behind the same reverse proxy, or host static files from the same domain
- React Query manages caching, polling (30s default), loading/error states
- `src/api/client.ts` wraps `fetch()` and throws `ApiError` on non-2xx
- Each page's hook (`useOverview`, `useQueues` etc.) maps to one API endpoint

---

## Backend API endpoints

All paths: `GET /dashboard/api/{endpoint}`

| Endpoint              | Data source                                        |
|-----------------------|----------------------------------------------------|
| `overview`            | COUNT aggregates across all tables                 |
| `slides`              | `core.file_records` (paginated)                    |
| `slides/{id}`         | `core.file_records` + `qc.*` + `dicomizer.*` + `uploader.*` + `events.*` |
| `events/recent`       | `events.pipeline_events` (latest N)                |
| `queues`              | `core.service_trigger` grouped by service + status |
| `recovery`            | `failed_watcher.technician_changes`                |
| `failures`            | Failed `core.file_records` + failed `core.service_trigger` |
| `services/health`     | `core.runner_registrations`                        |

---

## Backend environment variables

| Variable              | Default       | Description            |
|-----------------------|---------------|------------------------|
| `DATABASE_URL`        | *(required)*  | PostgreSQL connection string |
| `DASHBOARD_HOST`      | `127.0.0.1`   | Bind address           |
| `DASHBOARD_PORT`      | `8090`        | Listen port            |
| `DASHBOARD_LOG_LEVEL` | `info`        | Uvicorn log level      |
| `DASHBOARD_RELOAD`    | `false`       | Hot-reload (dev only)  |

---

## Testing

```bash
# Backend unit tests (no DB required)
pytest tests/unit/test_dashboard_api.py -v

# Frontend TypeScript type check
cd dashboard-ui && npx tsc --noEmit

# Frontend production build
cd dashboard-ui && npm run build
```

---

## Future integration points

The frontend is structured to make these additions straightforward:

- **AI Copilot**: a `CopilotPanel` component can be added to `Shell.tsx` as a right-side drawer
- **RBAC**: `Sidebar.tsx` has a placeholder `RBAC: not configured` — wire `src/context/AuthContext.tsx` when ready
- **Multi-site**: the site selector in `Sidebar.tsx` is a static label — replace with a dropdown fetching from a future `/dashboard/api/sites` endpoint
- **Alerts**: `TopBar.tsx` has a notification bell slot — connect to a future `useAlerts()` hook
- **Dark mode**: fully supported via Tailwind `dark:` classes + `ThemeProvider` with localStorage persistence

---

## Security notes

- The backend binds to `127.0.0.1` by default — do not expose on `0.0.0.0` without a TLS-terminating reverse proxy
- The frontend is read-only — no write operations in any component
- No secrets are ever returned by the API
