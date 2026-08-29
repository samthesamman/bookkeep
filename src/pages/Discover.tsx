import { BookRow } from '@/components/books/BookRow';
import { BookRowSkeleton } from '@/components/books/BookRowSkeleton';
import { RequestsRow } from '@/components/books/RequestsRow';
import { useBestsellers, useDiscoverStatus } from '@/hooks/useBestsellers';
import { useQuery } from '@tanstack/react-query';
import { requestsApi } from '@/lib/api';
import type { BookRequest } from '@/types/book';
import { AlertCircle, Settings, ExternalLink, Sparkles } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Link } from 'react-router-dom';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function Discover() {
  const { data: status, isLoading: statusLoading } = useDiscoverStatus();
  const hasKey = status?.has_nyt_key ?? false;

  const { data: requests = [] } = useQuery({
    queryKey: ['requests', 'recent'],
    queryFn: () => requestsApi.getAll(0, 4),
    enabled: hasKey,
  });

  const recentRequests: BookRequest[] = requests
    .filter((req: any) => req.book)
    .map((req: any) => ({
      id: String(req.id),
      bookId: String(req.book?.hardcover_id || req.book_id),
      book: {
        id: String(req.book.id),
        title: req.book.title || 'Unknown Title',
        author: req.book.author || 'Unknown Author',
        cover: req.book.cover_url || '/placeholder.svg',
        description: req.book.description || '',
        publishedDate: req.book.published_date || '',
        genres: req.book.genres ? (typeof req.book.genres === 'string' ? req.book.genres.split(',') : req.book.genres) : [],
        rating: req.book.rating || 0,
        series: req.book.series,
        seriesPosition: req.book.series_position,
        hardcoverId: req.book.hardcover_id,
        hardcoverSlug: req.book.hardcover_slug,
        isbn: req.book.isbn,
        pageCount: req.book.page_count,
      },
      userId: String(req.user_id),
      userName: req.user?.username || req.user?.full_name || 'Unknown User',
      format: req.format,
      status: req.status,
      notes: req.notes,
      adminNotes: req.admin_notes,
      createdAt: req.created_at,
      updatedAt: req.updated_at,
    }));

  const { data: bestsellers, isLoading: bestsellersLoading, error: bestsellersError } = useBestsellers();
  const lists = bestsellers?.lists ?? [];

  const discoverBooks = lists.flatMap((list) => list.books);
  const discoverHardcoverIds = Array.from(
    new Set(
      discoverBooks
        .map((book) => book.hardcoverId ?? Number(book.id))
        .filter((bookId) => Number.isFinite(bookId))
        .map((bookId) => Number(bookId))
    )
  );

  const { data: discoverRequestStatuses } = useQuery({
    queryKey: ['requests', 'by-hardcover', 'discover', discoverHardcoverIds],
    queryFn: () => requestsApi.getByHardcoverBatch(discoverHardcoverIds),
    enabled: discoverHardcoverIds.length > 0,
    staleTime: 5 * 60 * 1000,
  });

  const discoverRequestStatusMap = new Map(
    discoverRequestStatuses?.results.map((item) => [item.hardcover_id, item]) ?? []
  );

  // Show splash page if the NYT Books API key is not configured
  if (!statusLoading && !hasKey) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Card className="max-w-2xl w-full bg-card/50 backdrop-blur-xl border-border/50 shadow-2xl">
          <CardHeader className="text-center pb-2">
            <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20 shadow-lg shadow-primary/10">
              <Settings className="h-10 w-10 text-primary" />
            </div>
            <CardTitle className="text-3xl font-bold text-foreground tracking-tight">
              NYT Books API Key Required
            </CardTitle>
            <CardDescription className="text-base mt-3 text-muted-foreground">
              The Discover page shows the New York Times Best Sellers. Set the{' '}
              <code className="text-foreground">NYT_BOOKS_API_KEY</code> environment variable to enable it.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6 pt-4">
            <div className="text-center">
              <p className="text-sm text-muted-foreground">
                Register an app and enable the Books API at{' '}
                <a
                  href="https://developer.nytimes.com/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary font-medium hover:underline underline-offset-4 inline-flex items-center gap-1.5 transition-colors"
                >
                  developer.nytimes.com
                  <ExternalLink className="h-3 w-3" />
                </a>
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Hero section */}
      <div className="relative mb-10 -mx-4 sm:-mx-6 lg:-mx-8 px-4 sm:px-6 lg:px-8 py-8 overflow-hidden">
        {/* Background gradient */}
        <div className="absolute inset-0 bg-gradient-to-b from-primary/5 via-transparent to-transparent" />
        <div className="absolute top-0 left-1/4 w-96 h-96 bg-primary/10 rounded-full blur-[100px] hidden md:block" />
        <div className="absolute top-0 right-1/4 w-64 h-64 bg-amber-500/5 rounded-full blur-[80px] hidden md:block" />

        <div className="relative">
          <div className="flex items-center gap-3 mb-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 border border-primary/20">
              <Sparkles className="h-5 w-5 text-primary" />
            </div>
            <span className="text-sm font-medium text-primary uppercase tracking-wider">Welcome back</span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-bold text-foreground tracking-tight">
            Discover Books
          </h1>
          <p className="mt-3 text-lg text-muted-foreground max-w-2xl">
            This week's New York Times Best Sellers. Your next favorite book is waiting.
          </p>
        </div>
      </div>

      {bestsellersError && (
        <Alert variant="destructive" className="mb-6 rounded-xl border-rose-500/30 bg-rose-500/10">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            Failed to load the Best Sellers lists. Please try again later.
          </AlertDescription>
        </Alert>
      )}

      {bestsellersLoading ? (
        <>
          <BookRowSkeleton title="Combined Print & E-Book Fiction" />
          <BookRowSkeleton title="Combined Print & E-Book Nonfiction" />
        </>
      ) : (
        <>
          {lists.slice(0, 1).map((list) => (
            <BookRow
              key={list.listNameEncoded}
              title={list.listName}
              books={list.books}
              requestStatusMap={discoverRequestStatusMap}
            />
          ))}

          {/* Recent Requests */}
          <RequestsRow
            title="Recent Requests"
            requests={recentRequests}
            viewAllLink="/requests"
          />

          {lists.slice(1).map((list) => (
            <BookRow
              key={list.listNameEncoded}
              title={list.listName}
              books={list.books}
              requestStatusMap={discoverRequestStatusMap}
            />
          ))}

          {lists.length === 0 && !bestsellersError && (
            <Alert className="rounded-xl">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                No Best Sellers to show yet. An admin can choose which lists appear in{' '}
                <Link to="/settings" className="text-primary hover:underline">Settings</Link>.
              </AlertDescription>
            </Alert>
          )}
        </>
      )}

      {bestsellers?.attribution && (
        <p className="pt-6 text-xs text-muted-foreground">{bestsellers.attribution}</p>
      )}
    </div>
  );
}
