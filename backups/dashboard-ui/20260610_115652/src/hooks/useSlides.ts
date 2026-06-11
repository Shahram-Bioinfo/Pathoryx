import { useQuery } from '@tanstack/react-query'
import { fetchSlides, type SlideListParams } from '../api/slides'

export function useSlides(params: SlideListParams = {}) {
  return useQuery({
    queryKey: ['slides', params],
    queryFn: () => fetchSlides(params),
    staleTime: 20_000,
    placeholderData: (prev) => prev,
  })
}
