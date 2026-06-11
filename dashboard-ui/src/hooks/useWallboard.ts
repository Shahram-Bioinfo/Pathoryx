import { useQuery } from '@tanstack/react-query'
import { fetchWallboard } from '../api/wallboard'

export function useWallboard() {
  return useQuery({
    queryKey: ['wallboard'],
    queryFn: fetchWallboard,
    refetchInterval: 10_000,
    staleTime: 10_000,
    retry: 2,
  })
}
