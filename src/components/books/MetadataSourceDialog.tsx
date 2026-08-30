import { useEffect, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, RotateCcw, Search, Star } from 'lucide-react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { calibreApi, type MetadataCandidate, type MetadataSource } from '@/lib/api';
import { formatRating } from '@/lib/utils';

const SOURCE_LABEL: Record<MetadataSource, string> = {
  current: 'Current',
  googlebooks: 'Google Books',
  applebooks: 'Apple Books',
  openlibrary: 'Open Library',
  hardcover: 'Hardcover',
};

function stripHtml(s: string | null): string {
  if (!s) return '';
  return s.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

const EMPTY = <span className="text-muted-foreground/50">—</span>;

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value?: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[70px_1fr] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? 'break-all font-mono text-foreground' : 'text-foreground'}>
        {value || EMPTY}
      </dd>
    </div>
  );
}

function CandidateColumn({
  candidate,
  isCurrent,
  onApply,
  applying,
  disabled,
}: {
  candidate: MetadataCandidate;
  isCurrent: boolean;
  onApply: () => void;
  applying: boolean;
  disabled: boolean;
}) {
  const desc = stripHtml(candidate.description);
  const unavailable = !isCurrent && candidate.found === false;
  return (
    <div className="flex w-80 shrink-0 flex-col rounded-lg border border-border bg-card/50">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <span className="text-sm font-semibold text-foreground">
          {SOURCE_LABEL[candidate.source]}
        </span>
        {isCurrent ? (
          <Badge variant="outline" className="border-border text-[10px] text-muted-foreground">
            in use
          </Badge>
        ) : (
          <Button size="sm" variant="secondary" disabled={disabled || unavailable} onClick={onApply}>
            {applying ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Check className="mr-1 h-3.5 w-3.5" />
            )}
            Use this
          </Button>
        )}
      </div>

      {unavailable ? (
        <div className="flex flex-1 items-center p-3 text-xs text-muted-foreground" style={{ minHeight: 120 }}>
          {candidate.note || 'Nothing returned.'}
        </div>
      ) : (
        <div className="flex-1 space-y-3 overflow-y-auto p-3" style={{ maxHeight: 460 }}>
          <div className="mx-auto h-36 w-24 shrink-0 overflow-hidden rounded border border-border bg-muted">
            {candidate.cover_url ? (
              <img
                src={candidate.cover_url}
                alt=""
                className="h-full w-full object-cover"
                onError={(e) => ((e.target as HTMLImageElement).style.visibility = 'hidden')}
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-[10px] text-muted-foreground">
                no cover
              </div>
            )}
          </div>

          <dl className="space-y-1.5 text-xs">
            <Field label="Title" value={candidate.title} />
            <Field label="Author" value={candidate.author} />
            <Field label="Publisher" value={candidate.publisher} />
            <Field label="ISBN" value={candidate.isbn} mono />
            <Field
              label="Published"
              value={candidate.published_date ? candidate.published_date.slice(0, 10) : null}
            />
            <Field
              label="Length"
              value={candidate.page_count ? `${candidate.page_count} pages` : null}
            />
            <Field
              label="Rating"
              value={
                candidate.rating ? (
                  <span className="inline-flex items-center gap-1 text-amber-500">
                    <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                    {formatRating(candidate.rating)}
                    {candidate.ratings_count ? (
                      <span className="text-muted-foreground">({candidate.ratings_count})</span>
                    ) : null}
                  </span>
                ) : null
              }
            />
            <Field
              label="Series"
              value={
                candidate.series
                  ? `${candidate.series}${
                      candidate.series_position ? ` #${candidate.series_position}` : ''
                    }`
                  : null
              }
            />
            <Field
              label="Subjects"
              value={
                candidate.genres.length > 0 ? (
                  <span className="inline-flex flex-wrap gap-1">
                    {candidate.genres.map((g) => (
                      <span
                        key={g}
                        className="inline-flex items-center rounded-full bg-secondary px-2 py-0.5 text-[10px] font-semibold text-secondary-foreground"
                      >
                        {g}
                      </span>
                    ))}
                  </span>
                ) : null
              }
            />
          </dl>

          <div>
            <p className="mb-1 text-xs text-muted-foreground">Description</p>
            <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground">
              {desc || <span className="italic text-muted-foreground/50">—</span>}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

/** Admin: compare a Calibre book's metadata across Google Books / Open Library /
 *  Hardcover and apply the chosen source to the linked bookkeep record. */
export function MetadataSourceDialog({
  calibreId,
  open,
  onOpenChange,
  onApplied,
}: {
  calibreId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied?: () => void;
}) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<MetadataSource | null>(null);
  // `searchTitle` (undefined = use the stored title) drives the query;
  // `titleInput` is the editable box.
  const [titleInput, setTitleInput] = useState('');
  const [searchTitle, setSearchTitle] = useState<string | undefined>(undefined);
  const [storedTitle, setStoredTitle] = useState<string | null>(null);

  // Reset when the dialog is closed so it re-seeds on next open.
  useEffect(() => {
    if (!open) {
      setSearchTitle(undefined);
      setStoredTitle(null);
    }
  }, [open]);

  const { data, isLoading, isFetching, isError, error } = useQuery({
    queryKey: ['calibre-metadata-candidates', calibreId, searchTitle ?? ''],
    queryFn: () => calibreApi.metadataCandidates(calibreId, { title: searchTitle }),
    enabled: open,
    staleTime: 60 * 1000,
    retry: false,
  });

  // Seed the editable title from the stored value once it loads.
  useEffect(() => {
    if (data && storedTitle === null) {
      setStoredTitle(data.current.title ?? '');
      setTitleInput(data.current.title ?? '');
    }
  }, [data, storedTitle]);

  const runSearch = () => {
    const t = titleInput.trim();
    setSearchTitle(t && t !== storedTitle ? t : undefined);
  };
  const resetTitle = () => {
    setTitleInput(storedTitle ?? '');
    setSearchTitle(undefined);
  };
  const overriding = searchTitle !== undefined;

  const applyMutation = useMutation({
    mutationFn: (source: Exclude<MetadataSource, 'current'>) =>
      calibreApi.applyMetadata(calibreId, { source, title: searchTitle }),
    onMutate: (source) => setPending(source),
    onSuccess: (_res, source) => {
      toast.success(`Applied ${SOURCE_LABEL[source]} metadata`);
      queryClient.invalidateQueries({ queryKey: ['calibre-book', calibreId] });
      queryClient.invalidateQueries({ queryKey: ['calibre-books'] });
      queryClient.invalidateQueries({ queryKey: ['calibre-cover', calibreId] });
      queryClient.invalidateQueries({ queryKey: ['calibre-metadata-candidates', calibreId] });
      queryClient.invalidateQueries({ queryKey: ['book', 'by-hardcover'] });
      queryClient.invalidateQueries({ queryKey: ['calibre', 'by-hardcover'] });
      onApplied?.();
      onOpenChange(false);
    },
    onError: (err: Error) =>
      toast.error('Could not apply metadata', { description: err.message }),
    onSettled: () => setPending(null),
  });

  const columns: MetadataCandidate[] = data
    ? [data.current, ...data.candidates]
    : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[min(95vw,1100px)]">
        <DialogHeader>
          <DialogTitle>Choose a metadata source</DialogTitle>
          <DialogDescription>
            What each source returns for this book. “Use this” overwrites the
            stored record — title, author, description, cover, length, subjects
            (series and rating come from Hardcover; ISBN is filled only if the
            book has none; publisher is shown for comparison only). Edit the
            search title first if the stored one is wrong.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={titleInput}
              onChange={(e) => setTitleInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') runSearch();
              }}
              placeholder="Search title"
              className="pl-9"
            />
          </div>
          <Button
            variant="secondary"
            onClick={runSearch}
            disabled={isFetching || !titleInput.trim()}
          >
            {isFetching ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
            Search
          </Button>
          {overriding && (
            <Button variant="ghost" size="sm" onClick={resetTitle} disabled={isFetching}>
              <RotateCcw className="mr-1 h-3.5 w-3.5" />
              Stored title
            </Button>
          )}
        </div>
        {overriding && (
          <p className="text-xs text-muted-foreground">Searching for “{searchTitle}”.</p>
        )}

        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Searching Google Books, Open Library and Hardcover…
          </div>
        ) : isError ? (
          <p className="py-12 text-center text-sm text-destructive">
            {error instanceof Error ? error.message : 'Failed to load metadata.'}
          </p>
        ) : (
          <div
            className={`flex gap-3 overflow-x-auto pb-2 transition-opacity ${
              isFetching ? 'pointer-events-none opacity-50' : ''
            }`}
          >
            {columns.map((c) => (
              <CandidateColumn
                key={c.source}
                candidate={c}
                isCurrent={c.source === 'current'}
                applying={pending === c.source}
                disabled={applyMutation.isPending || isFetching}
                onApply={() =>
                  applyMutation.mutate(c.source as Exclude<MetadataSource, 'current'>)
                }
              />
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
