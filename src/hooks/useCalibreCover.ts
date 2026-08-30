import { useQuery } from '@tanstack/react-query';
import { calibreApi } from '@/lib/api';

/**
 * Fetch a Calibre book's embedded cover as a data: URL.
 *
 * Data URLs (unlike object URLs) need no revoke and survive being read from the
 * React Query cache by another component or after a route change — which is why
 * the grid and the detail page can safely share the ['calibre-cover', id] key.
 */
export function useCalibreCover(bookId: number, enabled: boolean) {
  return useQuery({
    queryKey: ['calibre-cover', bookId],
    queryFn: () => calibreApi.coverDataUrl(bookId),
    enabled: enabled && Number.isFinite(bookId),
    staleTime: 10 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
    retry: false,
  });
}
