import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { Headphones, Search, Clock, ListMusic, Loader2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { audiobookshelfApi, type AudiobookshelfLibraryItem } from '@/lib/api';

type Sort = 'title' | 'author' | 'added';

const SORT_OPTIONS: { value: Sort; label: string }[] = [
  { value: 'title', label: 'Title' },
  { value: 'author', label: 'Author' },
  { value: 'added', label: 'Recently added' },
];

function formatDuration(seconds: number | null): string | null {
  if (!seconds || seconds <= 0) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

/** Cover proxied through the backend with the bearer token (data URL). */
function AudiobookCover({ item }: { item: AudiobookshelfLibraryItem }) {
  const { data: url } = useQuery({
    queryKey: ['abs-cover', item.id],
    queryFn: () => audiobookshelfApi.coverDataUrl(item.id),
    enabled: item.has_cover,
    staleTime: 60 * 60_000,
    gcTime: 60 * 60_000,
    retry: false,
  });

  if (!item.has_cover || !url) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-muted">
        <Headphones className="h-8 w-8 text-muted-foreground/50" />
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={item.title}
      className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
      loading="lazy"
    />
  );
}

function AudiobookCard({ item }: { item: AudiobookshelfLibraryItem }) {
  const navigate = useNavigate();
  const [resolving, setResolving] = useState(false);
  const duration = formatDuration(item.duration_seconds);

  const openBook = async () => {
    if (resolving) return;
    const nav = (hardcoverId: number) =>
      navigate(`/book/${hardcoverId}`, {
        state: { from: '/my-audiobooks', fromLabel: 'Back to My Audiobooks' },
      });

    // Already matched: open straight away, but still fire resolve in the
    // background so the book gets linked to its Calibre eBook (if any).
    if (item.hardcover_id) {
      nav(item.hardcover_id);
      void audiobookshelfApi.resolveItem(item.id).catch(() => {});
      return;
    }

    setResolving(true);
    try {
      const res = await audiobookshelfApi.resolveItem(item.id);
      nav(res.hardcover_id);
    } catch (err) {
      toast.error('Could not open this audiobook', {
        description:
          err instanceof Error
            ? err.message
            : "We couldn't match it to a book.",
      });
    } finally {
      setResolving(false);
    }
  };

  return (
    <button
      type="button"
      onClick={openBook}
      className="group block w-full text-left"
    >
      <div className="book-cover-glow relative">
        <div className="book-cover aspect-square bg-card overflow-hidden rounded-lg border border-border">
          <AudiobookCover item={item} />
        </div>
        {resolving && (
          <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-background/60">
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
          </div>
        )}
      </div>
      <div className="mt-1.5 flex min-h-[5.75rem] flex-col gap-0.5">
        <h3 className="line-clamp-2 text-xs font-medium leading-snug text-foreground">
          {item.title}
        </h3>
        <p className="line-clamp-1 text-[11px] text-muted-foreground">
          {item.author ?? 'Unknown author'}
        </p>
        <p className="line-clamp-1 text-[11px] text-muted-foreground/80">
          {item.series ?? ' '}
        </p>
        {duration ? (
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <Clock className="h-3 w-3" />
            {duration}
          </span>
        ) : null}
      </div>
    </button>
  );
}

export default function MyAudiobooks() {
  const [searchInput, setSearchInput] = useState('');
  const [sort, setSort] = useState<Sort>('title');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['abs-library-items'],
    queryFn: () => audiobookshelfApi.getLibraryItems(),
    retry: false,
    staleTime: 5 * 60_000,
  });

  const notConfigured =
    isError && error instanceof Error && /not configured/i.test(error.message);

  const items = useMemo(() => {
    const list = data ?? [];
    const q = searchInput.trim().toLowerCase();
    const filtered = q
      ? list.filter(
          (i) =>
            i.title.toLowerCase().includes(q) ||
            (i.author ?? '').toLowerCase().includes(q) ||
            (i.series ?? '').toLowerCase().includes(q) ||
            (i.narrator ?? '').toLowerCase().includes(q),
        )
      : list.slice();

    filtered.sort((a, b) => {
      if (sort === 'title') return a.title.localeCompare(b.title);
      if (sort === 'author')
        return (a.author ?? '').localeCompare(b.author ?? '');
      return (b.added_at ?? 0) - (a.added_at ?? 0);
    });
    return filtered;
  }, [data, searchInput, sort]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
          <Headphones className="h-6 w-6 text-primary" />
          My Audiobooks
        </h1>
        <p className="mt-1 text-muted-foreground">
          Everything in your Audiobookshelf library.
          {data ? ` ${data.length} audiobook${data.length === 1 ? '' : 's'}.` : ''}
        </p>
      </div>

      {notConfigured ? (
        <div className="rounded-xl border border-dashed border-border py-16 text-center">
          <Headphones className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
          <h3 className="text-lg font-medium text-foreground">
            No Audiobookshelf server configured
          </h3>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            Ask an administrator to connect one under Settings → Services →
            Audiobookshelf.
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search by title, author, series, or narrator..."
                className="bg-secondary border-border pl-9"
              />
            </div>
            <Select value={sort} onValueChange={(v) => setSort(v as Sort)}>
              <SelectTrigger className="bg-secondary border-border sm:w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {isError && !notConfigured && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-500">
              {error instanceof Error
                ? error.message
                : 'Failed to load the Audiobookshelf library.'}
            </div>
          )}

          {isLoading ? (
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10">
              {Array.from({ length: 20 }).map((_, i) => (
                <div key={i} className="space-y-2">
                  <Skeleton className="aspect-square w-full rounded-lg" />
                  <Skeleton className="h-3 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              ))}
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border py-16 text-center">
              <ListMusic className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
              <h3 className="text-lg font-medium text-foreground">
                No audiobooks match
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {searchInput
                  ? 'Try a different search term.'
                  : 'This Audiobookshelf library is empty.'}
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10">
              {items.map((item) => (
                <AudiobookCard key={item.id} item={item} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
