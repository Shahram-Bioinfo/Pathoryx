import { useQuery } from '@tanstack/react-query'
import { fetchSlideDetail } from '../api/slides'

export function useSlideDetail(globalArtifactId: string | undefined) {
  return useQuery({
    queryKey: ['slide', globalArtifactId],
    queryFn: () => fetchSlideDetail(globalArtifactId!),
    enabled: !!globalArtifactId,
    staleTime: 30_000,
  })
}
