import { useQuery } from '@tanstack/react-query'
import { fetchMonitoredFiles, type MonitoredFilesParams } from '../api/watchFolders'

export function useMonitoredFiles(params: MonitoredFilesParams = {}) {
  return useQuery({
    queryKey: ['monitoredFiles', params],
    queryFn: () => fetchMonitoredFiles(params),
    refetchInterval: 30_000,
  })
}
