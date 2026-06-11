import { apiFetch } from './client'
import type { WallboardResponse } from '../types/api'

export const fetchWallboard = (): Promise<WallboardResponse> =>
  apiFetch<WallboardResponse>('/wallboard')
