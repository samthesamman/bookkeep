import { useQuery } from '@tanstack/react-query';
import { discoverApi } from '@/lib/api';
import { transformHardcoverBook } from '@/lib/hardcover';
import type { Book } from '@/types/book';

export interface BestsellerList {
  listName: string;
  listNameEncoded: string;
  updated: string | null;
  books: Book[];
}

export interface BestsellersResult {
  lists: BestsellerList[];
  attribution: string;
}

const DAY = 24 * 60 * 60 * 1000;

export function useBestsellers() {
  return useQuery<BestsellersResult>({
    queryKey: ['discover', 'bestsellers'],
    queryFn: async () => {
      const data = await discoverApi.getBestsellers();
      return {
        attribution: data.attribution,
        lists: (data.lists || []).map((list) => ({
          listName: list.list_name,
          listNameEncoded: list.list_name_encoded,
          updated: list.updated,
          books: (list.books || []).map(transformHardcoverBook),
        })),
      };
    },
    staleTime: DAY,
    gcTime: DAY,
  });
}

export function useDiscoverStatus() {
  return useQuery({
    queryKey: ['discover', 'status'],
    queryFn: () => discoverApi.getStatus(),
  });
}
