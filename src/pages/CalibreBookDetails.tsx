import { useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BookOpen,
  Calendar,
  Library,
  Link2,
  Link2Off,
  RefreshCw,
  Sparkles,
  Star,
} from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { CalibreFormatActions } from '@/components/books/CalibreFormatActions';
import { CalibreRelinkDialog } from '@/components/books/CalibreRelinkDialog';
import { calibreApi } from '@/lib/api';
import { formatRating } from '@/lib/utils';
import { useUser } from '@/contexts/UserContext';

const LINK_SOURCE_LABEL: Record<string, string> = {
  download: 'matched from your request',
  manual: 'linked by hand',
  fuzzy: 'auto-matched by title',
};

function formatDate(dateStr?: string | null) {
  if (!dateStr) return 'Unknown';
  const trimmed = dateStr.slice(0, 10);
  if (/^\d{4}$/.test(trimmed)) return trimmed;
  try {
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return trimmed;
  }
}

/** Full-page detail view for a book that lives only in the Calibre library.
 *  Books that are linked to a Hardcover record redirect to the canonical
 *  /book/:hardcoverId page so both entry points share one UI. */
export default function CalibreBookDetails() {
  const { id } = useParams();
  const calibreId = Number(id);
  const queryClient = useQueryClient();
  const { isAdmin } = useUser();
  const [relinkOpen, setRelinkOpen] = useState(false);
  const [busyLink, setBusyLink] = useState(false);

  const { data: book, isLoading, error } = useQuery({
    queryKey: ['calibre-book', calibreId],
    queryFn: () => calibreApi.getBook(calibreId),
    enabled: Number.isFinite(calibreId),
  });

  const { data: coverUrl } = useQuery({
    queryKey: ['calibre-cover', calibreId],
    queryFn: async () => URL.createObjectURL(await calibreApi.fetchCover(calibreId)),
    enabled: Number.isFinite(calibreId) && !!book?.has_cover && !book?.overlay_cover_url,
    retry: false,
    staleTime: 10 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });

  useEffect(() => {
    return () => {
      if (coverUrl) URL.revokeObjectURL(coverUrl);
    };
  }, [coverUrl]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['calibre-book', calibreId] });
    queryClient.invalidateQueries({ queryKey: ['calibre-books'] });
    queryClient.invalidateQueries({ queryKey: ['calibre-cover', calibreId] });
  };

  const handleRefresh = async () => {
    setBusyLink(true);
    try {
      await calibreApi.refreshMetadata(calibreId);
      toast.success('Metadata refreshed from Hardcover');
      invalidate();
    } catch (err) {
      toast.error('Refresh failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setBusyLink(false);
    }
  };

  const handleUnlink = async () => {
    setBusyLink(true);
    try {
      await calibreApi.clearLink(calibreId);
      toast.success('Reverted to Calibre metadata');
      invalidate();
    } catch (err) {
      toast.error('Failed to unlink', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setBusyLink(false);
    }
  };

  if (!Number.isFinite(calibreId)) {
    return <Navigate to="/my-books" replace />;
  }

  // Linked to a Hardcover book — use the shared detail page instead.
  if (book?.hardcover_id) {
    return <Navigate to={`/book/${book.hardcover_id}`} replace />;
  }

  if (isLoading) {
    return (
      <div className="space-y-8 animate-fade-in">
        <Skeleton className="h-6 w-32 rounded-lg" />
        <div className="flex flex-col gap-8 md:flex-row">
          <Skeleton className="mx-auto h-80 w-52 rounded-xl md:mx-0 md:w-64" />
          <div className="flex-1 space-y-4">
            <Skeleton className="h-10 w-3/4 rounded-lg" />
            <Skeleton className="h-6 w-1/2 rounded-lg" />
            <Skeleton className="h-32 w-full rounded-lg" />
          </div>
        </div>
      </div>
    );
  }

  if (error || !book) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center">
        <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-2xl bg-muted/30">
          <BookOpen className="h-10 w-10 text-muted-foreground/50" />
        </div>
        <h1 className="mb-3 text-2xl font-bold text-foreground">Book Not Found</h1>
        <p className="mb-6 max-w-md text-center text-muted-foreground">
          {error instanceof Error ? error.message : "We couldn't find that library book."}
        </p>
        <Link to="/my-books">
          <Button variant="outline" className="h-11 rounded-xl px-6">
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back to My Books
          </Button>
        </Link>
      </div>
    );
  }

  const cover = book.overlay_cover_url || coverUrl;
  const tags = [...new Set([...(book.genres ?? []), ...book.tags])];
  const isbn =
    book.identifiers.isbn || book.identifiers.isbn13 || book.identifiers.isbn10 || null;

  return (
    <>
      <div className="space-y-10 animate-fade-in-up">
        <Link
          to="/my-books"
          className="group inline-flex items-center gap-2 text-muted-foreground transition-colors duration-300 hover:text-primary"
        >
          <ArrowLeft className="h-4 w-4 transition-transform duration-300 group-hover:-translate-x-1" />
          <span className="font-medium">Back to My Books</span>
        </Link>

        <div className="relative overflow-hidden rounded-3xl">
          {cover && (
            <div className="absolute inset-0 hidden md:block">
              <img
                src={cover}
                alt=""
                className="h-full w-full scale-125 object-cover opacity-20 blur-3xl"
              />
              <div className="absolute inset-0 bg-gradient-to-r from-background via-background/95 to-background/80" />
              <div className="absolute inset-0 bg-gradient-to-t from-background via-transparent to-background/50" />
            </div>
          )}

          <div className="relative flex flex-col gap-8 p-6 md:flex-row md:p-10 lg:gap-12">
            <div className="mx-auto flex-shrink-0 md:mx-0">
              <div className="book-cover-glow">
                <div className="book-cover aspect-[2/3] w-52 md:w-64">
                  {cover ? (
                    <img src={cover} alt={book.title} className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center bg-muted">
                      <BookOpen className="h-10 w-10 text-muted-foreground/50" />
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="flex-1 space-y-5 text-center md:text-left">
              {book.series && (
                <p className="text-sm font-medium text-primary">
                  {book.series}
                  {book.series_index ? ` #${Number(book.series_index)}` : ''}
                </p>
              )}

              <h1 className="text-3xl font-bold leading-tight tracking-tight text-foreground md:text-4xl lg:text-5xl">
                {book.title}
              </h1>

              {book.authors && (
                <Link
                  to={`/author?name=${encodeURIComponent(book.authors)}`}
                  className="inline-block text-xl font-medium text-primary underline-offset-4 transition-colors hover:underline"
                >
                  {book.authors}
                </Link>
              )}

              <div className="flex flex-wrap justify-center gap-5 text-sm md:justify-start">
                {book.rating ? (
                  <div className="flex items-center gap-2">
                    <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                    <span className="font-semibold text-foreground">
                      {formatRating(book.rating)}
                    </span>
                  </div>
                ) : null}
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Calendar className="h-4 w-4" />
                  <span>{formatDate(book.pubdate)}</span>
                </div>
                {book.page_count ? (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <BookOpen className="h-4 w-4" />
                    <span>{book.page_count} pages</span>
                  </div>
                ) : null}
                {book.publisher && (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <span>{book.publisher}</span>
                  </div>
                )}
                {isbn && (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <span className="font-mono text-xs">ISBN: {isbn}</span>
                  </div>
                )}
                {book.languages.length > 0 && (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <span>{book.languages.join(', ')}</span>
                  </div>
                )}
              </div>

              {tags.length > 0 && (
                <div className="flex flex-wrap justify-center gap-2 md:justify-start">
                  {tags.map((tag) => (
                    <Badge
                      key={tag}
                      variant="secondary"
                      className="rounded-lg border-border/50 bg-muted/50 px-3 py-1 font-medium text-muted-foreground"
                    >
                      {tag}
                    </Badge>
                  ))}
                </div>
              )}

              {book.description && (
                <div className="max-w-2xl">
                  <h2 className="mb-3 text-lg font-semibold text-foreground">Description</h2>
                  <div
                    className="prose prose-sm prose-invert max-w-none leading-relaxed text-muted-foreground [&_a]:text-primary"
                    dangerouslySetInnerHTML={{ __html: book.description }}
                  />
                </div>
              )}

              {(book.metadata_source === 'overlay' || isAdmin) && (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-secondary/30 p-3 text-xs">
                  {book.metadata_source === 'overlay' ? (
                    <>
                      <Badge variant="outline" className="gap-1 border-primary/40 text-primary">
                        <Sparkles className="h-3 w-3" />
                        Enriched from Hardcover
                      </Badge>
                      <span className="text-muted-foreground">
                        {book.link_source ? LINK_SOURCE_LABEL[book.link_source] : ''}
                        {book.link_source === 'fuzzy' && !book.link_confirmed && isAdmin
                          ? ' — change it if this is wrong'
                          : ''}
                      </span>
                      {isAdmin && (
                        <span className="ml-auto flex gap-1">
                          <Button variant="ghost" size="sm" disabled={busyLink} onClick={handleRefresh}>
                            <RefreshCw
                              className={`mr-1 h-3.5 w-3.5 ${busyLink ? 'animate-spin' : ''}`}
                            />
                            Refresh
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busyLink}
                            onClick={() => setRelinkOpen(true)}
                          >
                            <Link2 className="mr-1 h-3.5 w-3.5" />
                            Change
                          </Button>
                          <Button variant="ghost" size="sm" disabled={busyLink} onClick={handleUnlink}>
                            <Link2Off className="mr-1 h-3.5 w-3.5" />
                            Unlink
                          </Button>
                        </span>
                      )}
                    </>
                  ) : (
                    <>
                      <span className="text-muted-foreground">Metadata from Calibre only.</span>
                      {isAdmin && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="ml-auto"
                          disabled={busyLink}
                          onClick={() => setRelinkOpen(true)}
                        >
                          <Link2 className="mr-1 h-3.5 w-3.5" />
                          Link a Hardcover book
                        </Button>
                      )}
                    </>
                  )}
                </div>
              )}

              <div className="rounded-2xl border border-border/50 bg-card/30 p-5 text-left">
                <div className="mb-4 flex items-center gap-2">
                  <Library className="h-4 w-4 text-primary" />
                  <h2 className="text-sm font-semibold text-foreground">In your library</h2>
                </div>
                {book.format_details.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No downloadable files for this book.
                  </p>
                ) : (
                  <CalibreFormatActions
                    calibreBookId={book.id}
                    formats={book.format_details}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <CalibreRelinkDialog
        calibreId={calibreId}
        open={relinkOpen}
        onOpenChange={setRelinkOpen}
        onLinked={invalidate}
      />
    </>
  );
}
