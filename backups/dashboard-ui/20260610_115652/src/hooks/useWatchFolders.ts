import { useQuery } from '@tanstack/react-query'
import { fetchWatchFolders } from '../api/watchFolders'

export function useWatchFolders() {
  return useQuery({
    queryKey: ['watchFolders'],
    queryFn: fetchWatchFolders,
    refetchInterval: 30_000,
  })
}
