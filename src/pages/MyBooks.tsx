import { useEffect, useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  Library,
  Search,
  Star,
  ChevronLeft,
  ChevronRight,
  Download,
  BookOpen,
  Loader2,
  Mail,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { calibreApi, type CalibreBook, type CalibreSort } from '@/lib/api';
import { formatRating } from '@/lib/utils';
import { useUser } from '@/contexts/UserContext';

const PAGE_SIZE = 60;

const SORT_OPTIONS: { value: CalibreSort; label: string }[] = [
  { value: 'added', label: 'Recently added' },
  { value: 'title', label: 'Title' },
  { value: 'author', label: 'Author' },
  { value: 'pubdate', label: 'Publish date' },
];

/** Cover image fetched with the auth token and rendered from an object URL. */
function CalibreCover({ book }: { book: CalibreBook }) {
  const { data: url } = useQuery({
    queryKey: ['calibre-cover', book.id],
    queryFn: async () => URL.createObjectURL(await calibreApi.fetchCover(book.id)),
    enabled: book.has_cover,
    staleTime: 10 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    retry: false,
  });

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  if (!url) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-muted">
        <BookOpen className="h-8 w-8 text-muted-foreground/50" />
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={book.title}
      className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
      loading="lazy"
    />
  );
}

function CalibreBookCard({ book, onOpen }: { book: CalibreBook; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} className="group block text-left">
      <div className="book-cover-glow">
        <div className="book-cover aspect-[2/3] bg-card overflow-hidden rounded-lg border border-border">
          <CalibreCover book={book} />
        </div>
      </div>
      <div className="mt-1.5 space-y-0.5">
        <h3 className="line-clamp-2 text-xs font-medium leading-snug text-foreground">
          {book.title}
        </h3>
        <p className="line-clamp-1 text-[11px] text-muted-foreground">{book.authors}</p>
        {book.series && (
          <p className="line-clamp-1 text-[11px] text-muted-foreground/80">
            {book.series}
            {book.series_index ? ` #${Number(book.series_index)}` : ''}
          </p>
        )}
        {book.rating ? (
          <span className="inline-flex items-center gap-1 text-[11px] text-amber-500">
            <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
            {formatRating(book.rating)}
          </span>
        ) : null}
      </div>
    </button>
  );
}

function BookDetailSheet({
  bookId,
  open,
  onOpenChange,
}: {
  bookId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [downloading, setDownloading] = useState<string | null>(null);
  const [emailing, setEmailing] = useState<string | null>(null);
  const { user } = useUser();
  const deliveryEmail = user?.book_delivery_email || '';

  const { data: book, isLoading } = useQuery({
    queryKey: ['calibre-book', bookId],
    queryFn: () => calibreApi.getBook(bookId as number),
    enabled: open && bookId != null,
  });

  const { data: coverUrl } = useQuery({
    queryKey: ['calibre-cover', bookId],
    queryFn: async () => URL.createObjectURL(await calibreApi.fetchCover(bookId as number)),
    enabled: open && bookId != null && !!book?.has_cover,
    retry: false,
  });

  const handleDownload = async (format: string, name: string) => {
    setDownloading(format);
    try {
      await calibreApi.downloadFormat(bookId as number, format, `${name}.${format.toLowerCase()}`);
    } catch (error) {
      toast.error('Download failed', {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setDownloading(null);
    }
  };

  const handleEmail = async (format: string) => {
    setEmailing(format);
    try {
      const result = await calibreApi.emailBook(bookId as number, format);
      toast.success('Book emailed', { description: result.message });
    } catch (error) {
      toast.error('Failed to email book', {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setEmailing(null);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        {isLoading || !book ? (
          <div className="space-y-6">
            <Skeleton className="h-8 w-3/4" />
            <div className="flex gap-6">
              <Skeleton className="h-48 w-32 rounded-lg" />
              <div className="flex-1 space-y-3">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-4 w-1/3" />
              </div>
            </div>
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl font-bold text-foreground">{book.title}</h2>
              <p className="text-muted-foreground">{book.authors}</p>
            </div>

            <div className="flex gap-6">
              <div className="h-48 w-32 shrink-0 overflow-hidden rounded-lg border border-border bg-muted">
                {coverUrl ? (
                  <img src={coverUrl} alt={book.title} className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full w-full items-center justify-center">
                    <BookOpen className="h-8 w-8 text-muted-foreground/50" />
                  </div>
                )}
              </div>
              <div className="flex-1 space-y-2 text-sm">
                {book.series && (
                  <p>
                    <span className="text-muted-foreground">Series: </span>
                    {book.series}
                    {book.series_index ? ` #${Number(book.series_index)}` : ''}
                  </p>
                )}
                {book.publisher && (
                  <p>
                    <span className="text-muted-foreground">Publisher: </span>
                    {book.publisher}
                  </p>
                )}
                {book.pubdate && (
                  <p>
                    <span className="text-muted-foreground">Published: </span>
                    {book.pubdate.slice(0, 10)}
                  </p>
                )}
                {book.languages.length > 0 && (
                  <p>
                    <span className="text-muted-foreground">Language: </span>
                    {book.languages.join(', ')}
                  </p>
                )}
                {book.rating ? (
                  <p className="inline-flex items-center gap-1 text-amber-500">
                    <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                    {formatRating(book.rating)}
                  </p>
                ) : null}
                {Object.entries(book.identifiers).map(([type, val]) => (
                  <p key={type}>
                    <span className="text-muted-foreground uppercase">{type}: </span>
                    {val}
                  </p>
                ))}
              </div>
            </div>

            {book.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {book.tags.map((tag) => (
                  <Badge key={tag} variant="secondary" className="text-xs">
                    {tag}
                  </Badge>
                ))}
              </div>
            )}

            {book.description && (
              <div
                className="prose prose-sm prose-invert max-w-none text-sm text-muted-foreground [&_a]:text-primary"
                dangerouslySetInnerHTML={{ __html: book.description }}
              />
            )}

            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-foreground">Download</h3>
              {book.format_details.length === 0 && (
                <p className="text-sm text-muted-foreground">No downloadable files for this book.</p>
              )}
              <div className="space-y-2">
                {book.format_details.map((fmt) => (
                  <div key={fmt.format} className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={downloading !== null}
                      onClick={() => handleDownload(fmt.format, fmt.name)}
                    >
                      {downloading === fmt.format ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <Download className="mr-2 h-4 w-4" />
                      )}
                      {fmt.format}
                      {fmt.size ? (
                        <span className="ml-1 text-xs text-muted-foreground">
                          ({(fmt.size / 1024 / 1024).toFixed(1)} MB)
                        </span>
                      ) : null}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={emailing !== null || !deliveryEmail}
                      title={
                        deliveryEmail
                          ? `Email this ${fmt.format} to ${deliveryEmail}`
                          : 'Set a delivery email under Settings first'
                      }
                      onClick={() => handleEmail(fmt.format)}
                    >
                      {emailing === fmt.format ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <Mail className="mr-2 h-4 w-4" />
                      )}
                      Email to myself
                    </Button>
                  </div>
                ))}
              </div>
              {!deliveryEmail && book.format_details.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Add a delivery email under{' '}
                  <Link to="/settings" className="text-primary underline">
                    Settings
                  </Link>{' '}
                  to email books to yourself.
                </p>
              )}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export default function MyBooks() {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<CalibreSort>('added');
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  // Debounce the search input.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
  }, [sort]);

  const { data, isLoading, isError, error, isFetching } = useQuery({
    queryKey: ['calibre-books', search, sort, page],
    queryFn: () => calibreApi.getBooks({ search, sort, page, pageSize: PAGE_SIZE }),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const notConfigured =
    isError && error instanceof Error && /not configured/i.test(error.message);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const rangeStart = data && data.total > 0 ? (page - 1) * PAGE_SIZE + 1 : 0;
  const rangeEnd = data ? Math.min(page * PAGE_SIZE, data.total) : 0;

  const books = useMemo(() => data?.books ?? [], [data]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
          <Library className="h-6 w-6 text-primary" />
          My Books
        </h1>
        <p className="mt-1 text-muted-foreground">
          Everything in your Calibre library.
          {data ? ` ${data.total} book${data.total === 1 ? '' : 's'}.` : ''}
        </p>
      </div>

      {notConfigured ? (
        <div className="rounded-xl border border-dashed border-border py-16 text-center">
          <Library className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
          <h3 className="text-lg font-medium text-foreground">No Calibre library configured</h3>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            Ask an administrator to set a Calibre Database Directory under
            Settings → Services → Calibre.
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
                placeholder="Search by title or author..."
                className="bg-secondary border-border pl-9"
              />
            </div>
            <Select value={sort} onValueChange={(v) => setSort(v as CalibreSort)}>
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
              {error instanceof Error ? error.message : 'Failed to load the Calibre library.'}
            </div>
          )}

          {isLoading ? (
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10">
              {Array.from({ length: 20 }).map((_, i) => (
                <div key={i} className="space-y-2">
                  <Skeleton className="aspect-[2/3] w-full rounded-lg" />
                  <Skeleton className="h-3 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              ))}
            </div>
          ) : books.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border py-16 text-center">
              <BookOpen className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
              <h3 className="text-lg font-medium text-foreground">No books match</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {search ? 'Try a different search term.' : 'This Calibre library is empty.'}
              </p>
            </div>
          ) : (
            <div
              className={`grid grid-cols-3 gap-3 transition-opacity sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10 ${
                isFetching ? 'opacity-60' : ''
              }`}
            >
              {books.map((book) => (
                <CalibreBookCard
                  key={book.id}
                  book={book}
                  onOpen={() => {
                    setSelectedId(book.id);
                    setSheetOpen(true);
                  }}
                />
              ))}
            </div>
          )}

          {data && data.total > PAGE_SIZE && (
            <div className="flex items-center justify-between pt-2">
              <p className="text-sm text-muted-foreground">
                {rangeStart}–{rangeEnd} of {data.total}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Prev
                </Button>
                <span className="text-sm text-muted-foreground">
                  Page {page} / {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      <BookDetailSheet bookId={selectedId} open={sheetOpen} onOpenChange={setSheetOpen} />
    </div>
  );
}
