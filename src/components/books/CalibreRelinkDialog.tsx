import { useEffect, useMemo, useState } from 'react';
import { Search, Loader2 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { toast } from 'sonner';
import { calibreApi, hardcoverApi } from '@/lib/api';
import { transformHardcoverBook, type HardcoverBook } from '@/lib/hardcover';

/** Search Hardcover and link the chosen book to a Calibre library entry. */
export function CalibreRelinkDialog({
  calibreId,
  open,
  onOpenChange,
  onLinked,
}: {
  calibreId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onLinked: () => void;
}) {
  const [term, setTerm] = useState('');
  const [query, setQuery] = useState('');
  const [linkingId, setLinkingId] = useState<number | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQuery(term.trim()), 350);
    return () => clearTimeout(t);
  }, [term]);

  const { data, isFetching } = useQuery({
    queryKey: ['hardcover-relink-search', query],
    queryFn: () => hardcoverApi.search(query, 15),
    enabled: query.length > 1,
  });

  const results = useMemo(
    () => (data?.books ?? []).map((b: HardcoverBook) => transformHardcoverBook(b)),
    [data],
  );

  const handleLink = async (hardcoverId: number) => {
    setLinkingId(hardcoverId);
    try {
      await calibreApi.linkBook(calibreId, { hardcover_id: hardcoverId });
      toast.success('Book linked', { description: 'Metadata will refresh from Hardcover.' });
      onLinked();
      onOpenChange(false);
    } catch (error) {
      toast.error('Failed to link book', {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLinkingId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Link to a Hardcover book</DialogTitle>
        </DialogHeader>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            autoFocus
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Search by title or author..."
            className="pl-9"
          />
        </div>
        <div className="max-h-80 space-y-1 overflow-y-auto">
          {isFetching && <p className="p-2 text-sm text-muted-foreground">Searching…</p>}
          {!isFetching && query.length > 1 && results.length === 0 && (
            <p className="p-2 text-sm text-muted-foreground">No matches.</p>
          )}
          {results.map((r) => (
            <button
              key={r.id}
              type="button"
              disabled={linkingId !== null}
              onClick={() => handleLink(r.hardcoverId)}
              className="flex w-full items-center gap-3 rounded-md p-2 text-left hover:bg-secondary disabled:opacity-50"
            >
              <img src={r.cover} alt="" className="h-14 w-10 shrink-0 rounded object-cover" />
              <span className="min-w-0 flex-1">
                <span className="line-clamp-1 block text-sm font-medium text-foreground">
                  {r.title}
                </span>
                <span className="line-clamp-1 block text-xs text-muted-foreground">
                  {r.author}
                  {r.publishedDate ? ` · ${String(r.publishedDate).slice(0, 4)}` : ''}
                </span>
              </span>
              {linkingId === r.hardcoverId && (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              )}
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
