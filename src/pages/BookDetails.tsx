import { useCallback, useEffect, useState } from 'react';
import { useParams, Link, useSearchParams, useNavigate, useLocation } from 'react-router-dom';
import { ArrowLeft, Star, Calendar, BookOpen, Tag, Clock, Users, Headphones, Library, ExternalLink, Trash2, Search, X, Download, Loader2 } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { RequestDialog } from '@/components/books/RequestDialog';
import { SearchReleaseDialog } from '@/components/books/SearchReleaseDialog';
import { BookCard } from '@/components/books/BookCard';
import { useBookDetails, useBookPrompts } from '@/hooks/useHardcoverBooks';
import { requestsApi, booksApi, calibreApi, audiobookshelfApi } from '@/lib/api';
import { transformHardcoverBook } from '@/lib/hardcover';
import { CalibreFormatActions } from '@/components/books/CalibreFormatActions';
import { CalibreRelinkDialog } from '@/components/books/CalibreRelinkDialog';
import { AudiobookshelfRelinkDialog } from '@/components/books/AudiobookshelfRelinkDialog';
import { MetadataSourceDialog } from '@/components/books/MetadataSourceDialog';
import { toast } from 'sonner';
import { useUser } from '@/contexts/UserContext';
import { usePageVisibility } from '@/hooks/usePageVisibility';
import { useCalibreCover } from '@/hooks/useCalibreCover';

export default function BookDetails() {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [requestOpen, setRequestOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchFormat, setSearchFormat] = useState<'ebook' | 'audiobook'>('ebook');
  const [searchSource, setSearchSource] = useState<'prowlarr' | undefined>(undefined);
  const [relinkOpen, setRelinkOpen] = useState(false);
  const [absRelinkOpen, setAbsRelinkOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [downloadingAudiobook, setDownloadingAudiobook] = useState(false);
  const queryClient = useQueryClient();
  const { user, isAdmin } = useUser();
  const isVisible = usePageVisibility();
  const bypassCache =
    searchParams.get('bypass_cache') === 'true' || searchParams.get('bypass_cache') === '1';

  const goBack = useCallback(() => {
    const backState = location.state as { from?: string } | null;
    // A real in-app history entry — go back through it so the previous page's
    // scroll position is restored (see ScrollRestoration).
    if (location.key !== 'default') {
      navigate(-1);
      return;
    }
    navigate(backState?.from ?? '/');
  }, [location.state, location.key, navigate]);

  // Press Esc to return to the previous page (unless a dialog is open).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (requestOpen || searchOpen || relinkOpen || absRelinkOpen || sourcesOpen) return;
      goBack();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [goBack, requestOpen, searchOpen, relinkOpen, absRelinkOpen, sourcesOpen]);

  const { data: book, isLoading, error } = useBookDetails(id);
  const hardcoverId = book?.hardcoverId ?? (book?.id ? Number(book.id) : undefined);
  const hasHardcoverId = Number.isFinite(hardcoverId);

  const { data: dbBook } = useQuery({
    queryKey: ['book', 'by-hardcover', hardcoverId],
    queryFn: () => booksApi.getByHardcoverId(hardcoverId as number),
    enabled: hasHardcoverId,
    staleTime: 30_000,
    refetchInterval: hasHardcoverId && isVisible ? 30_000 : false,
  });

  const { data: requestStatus } = useQuery({
    queryKey: ['requests', 'by-hardcover', hardcoverId],
    queryFn: () => requestsApi.getByHardcoverId(hardcoverId as number),
    enabled: hasHardcoverId,
    staleTime: 15 * 1000,
    refetchOnMount: true,
    refetchInterval: hasHardcoverId && isVisible ? 30_000 : false,
    gcTime: 5 * 60 * 1000,
  });

  // Is this book already in the Calibre library (via the metadata-overlay link)?
  const { data: calibre } = useQuery({
    queryKey: ['calibre', 'by-hardcover', hardcoverId],
    queryFn: () => calibreApi.getByHardcover(hardcoverId as number),
    enabled: hasHardcoverId,
    staleTime: 30_000,
  });
  const calibreEbookFormats =
    calibre?.format_details.filter((f) => calibre.ebook_formats.includes(f.format)) ?? [];
  const calibreAudioFormats =
    calibre?.format_details.filter((f) => calibre.audiobook_formats.includes(f.format)) ?? [];

  // Deep link into Audiobookshelf for a book that's linked to an ABS item.
  const { data: absWebUrl } = useQuery({
    queryKey: ['audiobookshelf', 'web-url'],
    queryFn: () => audiobookshelfApi.getWebUrl(),
    enabled: !!dbBook?.audiobookshelf_id,
    staleTime: 5 * 60 * 1000,
  });
  const listenNowUrl =
    dbBook?.audiobookshelf_id && absWebUrl?.url
      ? `${absWebUrl.url.replace(/\/$/, '')}/item/${dbBook.audiobookshelf_id}`
      : null;

  // For a linked book, the Calibre overlay (Calibre's own metadata merged with
  // the curated Book row) is exactly what "My Books" shows — use it here too.
  const { data: calBook } = useQuery({
    queryKey: ['calibre-book', calibre?.calibre_book_id],
    queryFn: () => calibreApi.getBook(calibre!.calibre_book_id),
    enabled: !!calibre?.calibre_book_id,
    staleTime: 30_000,
  });
  // The chosen metadata source's cover (overlay_cover_url) wins; fall back to
  // Calibre's embedded cover only when the source didn't provide one.
  const { data: calFileCover } = useCalibreCover(
    calibre?.calibre_book_id ?? 0,
    !!calBook?.has_cover && !calBook?.overlay_cover_url,
  );

  const invalidateCalibre = () => {
    queryClient.invalidateQueries({ queryKey: ['calibre', 'by-hardcover', hardcoverId] });
    queryClient.invalidateQueries({ queryKey: ['hardcover', 'book', id] });
    queryClient.invalidateQueries({ queryKey: ['book', 'by-hardcover', hardcoverId] });
    if (calibre) {
      queryClient.invalidateQueries({ queryKey: ['calibre-cover', calibre.calibre_book_id] });
      queryClient.invalidateQueries({
        queryKey: ['calibre-metadata-candidates', calibre.calibre_book_id],
      });
    }
  };
  const calibreLinkMutation = useMutation({
    mutationFn: async (action: 'refresh' | 'unlink') => {
      if (!calibre) return;
      if (action === 'refresh') return calibreApi.refreshMetadata(calibre.calibre_book_id);
      return calibreApi.clearLink(calibre.calibre_book_id);
    },
    onSuccess: (_d, action) => {
      invalidateCalibre();
      toast.success(action === 'refresh' ? 'Metadata refreshed' : 'Calibre link removed');
    },
    onError: (err: Error) => toast.error('Action failed', { description: err.message }),
  });

  const invalidateAbs = () => {
    queryClient.invalidateQueries({ queryKey: ['book', 'by-hardcover', hardcoverId] });
    queryClient.invalidateQueries({ queryKey: ['hardcover', 'book', id] });
    queryClient.invalidateQueries({ queryKey: ['audiobookshelf'] });
  };
  const absLinkMutation = useMutation({
    mutationFn: async (action: 'unlink') => {
      if (!hasHardcoverId || action !== 'unlink') return;
      return audiobookshelfApi.unlink(hardcoverId as number);
    },
    onSuccess: () => {
      invalidateAbs();
      toast.success('Audiobookshelf link removed');
    },
    onError: (err: Error) => toast.error('Action failed', { description: err.message }),
  });

  const ebookRequestStatus = requestStatus?.ebook ?? null;
  const audiobookRequestStatus = requestStatus?.audiobook ?? null;
  const ebookRequestAvailable = ebookRequestStatus === 'available';
  const audiobookRequestAvailable = audiobookRequestStatus === 'available';

  const { data: promptSummaries = [] } = useBookPrompts(
    hasHardcoverId ? (hardcoverId as number) : undefined,
    4,
    30
  );

  const clearRequestsMutation = useMutation({
    mutationFn: async () => {
      if (!hasHardcoverId) {
        throw new Error('Book does not have a Hardcover ID.');
      }
      return requestsApi.clearByHardcoverId(hardcoverId as number);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['requests'] });
      queryClient.invalidateQueries({ queryKey: ['requests', 'by-hardcover', hardcoverId] });
      toast.success('Request cleared', {
        description: `${data.deleted_count} request(s) removed.`,
      });
    },
    onError: (err: Error) => {
      toast.error('Failed to clear request', { description: err.message });
    },
  });

  const clearAvailabilityMutation = useMutation({
    mutationFn: async ({ bookId, formatType }: { bookId: number; formatType: 'ebook' | 'audiobook' }) => {
      return booksApi.clearAvailability(bookId, formatType);
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['book', 'by-hardcover', hardcoverId] });
      queryClient.invalidateQueries({ queryKey: ['requests', 'by-hardcover', hardcoverId] });
      const formatName = variables.formatType === 'ebook' ? 'eBook' : 'Audiobook';
      toast.success('Availability cleared', {
        description: `${formatName} availability has been reset. You can now re-download this book.`,
      });
    },
    onError: (err: Error, variables) => {
      const formatName = variables.formatType === 'ebook' ? 'eBook' : 'Audiobook';
      toast.error('Failed to clear availability', {
        description: `Could not clear ${formatName} availability: ${err.message}`
      });
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-8 animate-fade-in">
        <Skeleton className="h-6 w-32 rounded-lg" />
        <div className="relative rounded-2xl overflow-hidden bg-card/50 p-8">
          <div className="flex flex-col md:flex-row gap-8">
            <Skeleton className="w-48 md:w-56 h-80 rounded-xl mx-auto md:mx-0" />
            <div className="flex-1 space-y-4">
              <Skeleton className="h-10 w-3/4 rounded-lg" />
              <Skeleton className="h-6 w-1/2 rounded-lg" />
              <div className="flex gap-4">
                <Skeleton className="h-5 w-20 rounded-lg" />
                <Skeleton className="h-5 w-24 rounded-lg" />
                <Skeleton className="h-5 w-20 rounded-lg" />
              </div>
              <div className="flex gap-2">
                <Skeleton className="h-7 w-16 rounded-lg" />
                <Skeleton className="h-7 w-20 rounded-lg" />
                <Skeleton className="h-7 w-18 rounded-lg" />
              </div>
              <Skeleton className="h-32 w-full rounded-lg" />
              <Skeleton className="h-12 w-40 rounded-xl" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (error || !book) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh]">
        <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-muted/30 mb-6">
          <BookOpen className="h-10 w-10 text-muted-foreground/50" />
        </div>
        <h1 className="text-2xl font-bold text-foreground mb-3">Book Not Found</h1>
        <p className="text-muted-foreground mb-6 text-center max-w-md">
          {error?.message || "We couldn't find the book you're looking for."}
        </p>
        <Link to="/">
          <Button variant="outline" className="h-11 px-6 rounded-xl">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Go Back Home
          </Button>
        </Link>
      </div>
    );
  }

  const formatRating = (rating: number) => {
    return rating > 0 ? rating.toFixed(1) : 'N/A';
  };

  const formatDate = (dateStr: string) => {
    if (!dateStr) return 'Unknown';
    if (/^\d{4}$/.test(dateStr)) return dateStr;
    try {
      return new Date(dateStr).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric'
      });
    } catch {
      return dateStr;
    }
  };

  const cleanDescription = (html: string) => {
    if (!html) return 'No description available.';
    return html.replace(/<[^>]*>/g, '').trim();
  };

  const ebookAvailable =
    dbBook?.ebook_available ||
    book.ebookAvailable ||
    ebookRequestAvailable ||
    calibreEbookFormats.length > 0 ||
    false;
  const audiobookAvailable =
    dbBook?.audiobook_available ||
    book.audiobookAvailable ||
    audiobookRequestAvailable ||
    calibreAudioFormats.length > 0 ||
    false;
  const ebookNotFound = ebookRequestStatus === 'not_found';
  const audiobookNotFound = audiobookRequestStatus === 'not_found';
  const ebookRequested =
    Boolean(ebookRequestStatus) && !ebookNotFound && ebookRequestStatus !== 'available';
  const audiobookRequested =
    Boolean(audiobookRequestStatus) && !audiobookNotFound && audiobookRequestStatus !== 'available';
  const canRequestEbook = Boolean(user?.can_request_ebook) && !ebookAvailable;
  const canRequestAudiobook = Boolean(user?.can_request_audiobook) && !audiobookAvailable;
  const canRequestAnything = canRequestEbook || canRequestAudiobook;
  // The Prowlarr search/download flow is admin-only, regardless of the
  // can_download permission flag.
  const canDownload = isAdmin && Boolean(user?.can_download);
  const hasMissingFormat = !ebookAvailable || !audiobookAvailable;
  const preferredFormat =
    ebookAvailable && !audiobookAvailable
      ? 'audiobook'
      : audiobookAvailable && !ebookAvailable
        ? 'ebook'
        : undefined;
  const hasAnyRequests = ebookRequested || audiobookRequested;
  // Only offer "clear request" to someone who can actually act on it: an admin
  // (clears everyone's) or a user who made one of these requests themselves.
  const ownsARequest = Boolean(requestStatus?.ebook_mine || requestStatus?.audiobook_mine);
  const canClearRequests = isAdmin || ownsARequest;

  // The "local" view: the Calibre overlay for a linked book (what My Books
  // shows), else the local Book row. When it exists and either the book is in
  // Calibre or an admin curated it (metadata_locked), it wins over Hardcover;
  // otherwise Hardcover leads and local only fills gaps.
  const curated = !!dbBook?.metadata_locked;
  const preferLocal = !!calBook || curated;
  const pick = <T,>(local: T | undefined, remote: T | undefined): T | undefined =>
    preferLocal ? local ?? remote : remote ?? local;

  const clean = <T,>(v: T | null | undefined): T | undefined =>
    v == null || v === '' || (Array.isArray(v) && v.length === 0) ? undefined : v;

  const hcCover = book.cover && book.cover !== '/placeholder.svg' ? book.cover : undefined;
  const hcRating = book.rating && book.rating > 0 ? book.rating : undefined;
  const hcPages = book.pageCount && book.pageCount > 0 ? book.pageCount : undefined;
  const overlayGenres: string[] = Array.isArray(dbBook?.genres)
    ? (dbBook!.genres as string[]).filter(Boolean)
    : [];

  const stripTags = (s: string | null | undefined) =>
    clean(s?.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim());

  const local = calBook
    ? {
        title: clean(calBook.title),
        author: clean(calBook.authors),
        cover: clean(calBook.overlay_cover_url) ?? calFileCover,
        description: stripTags(calBook.description),
        genres: (calBook.genres ?? []).filter(Boolean),
        rating: calBook.rating && calBook.rating > 0 ? calBook.rating : undefined,
        pageCount: calBook.page_count && calBook.page_count > 0 ? calBook.page_count : undefined,
        publishedDate: clean(calBook.pubdate),
        series: clean(calBook.series),
        seriesPosition: clean(calBook.series_index),
      }
    : {
        title: clean(dbBook?.title),
        author: clean(dbBook?.author),
        cover: clean(dbBook?.cover_url),
        description: clean(dbBook?.description),
        genres: overlayGenres,
        rating: dbBook?.rating && dbBook.rating > 0 ? dbBook.rating : undefined,
        pageCount: dbBook?.page_count && dbBook.page_count > 0 ? dbBook.page_count : undefined,
        publishedDate: clean(dbBook?.published_date),
        series: clean(dbBook?.series),
        seriesPosition: clean(dbBook?.series_position),
      };

  const displayTitle = pick(local.title, book.title) || book.title;
  const displayAuthor = pick(local.author, book.author) || book.author;
  const displayCover = pick(local.cover, hcCover) || '/placeholder.svg';
  const displayDescription = pick(local.description, clean(book.description)) || '';
  const displayGenres =
    pick(clean(local.genres), clean(book.genres)) || [];
  const displayRating = pick(local.rating, hcRating) || 0;
  const displayPageCount = pick(local.pageCount, hcPages) || 0;
  const displayPublishedDate = pick(local.publishedDate, clean(book.publishedDate)) || '';
  const displaySeries = pick(local.series, clean(book.series));
  const displaySeriesPosition = pick(local.seriesPosition, clean(book.seriesPosition));
  const displaySeriesId = book.seriesId ?? dbBook?.series_id ?? undefined;

  const seriesText = displaySeries
    ? `${displaySeries}${displaySeriesPosition ? ` #${displaySeriesPosition}` : ''}`
    : null;
  // Rendered in two places (the compact mobile header and the desktop info
  // column), so keep it here rather than duplicating the link/plain-text branch.
  const renderSeries = (className: string) => {
    if (!seriesText) return null;
    return displaySeriesId ? (
      <Link
        to={`/series/${displaySeriesId}`}
        className={`${className} text-primary hover:underline underline-offset-4`}
      >
        {seriesText}
      </Link>
    ) : (
      <span className={`${className} text-muted-foreground`}>{seriesText}</span>
    );
  };

  const authorHref = `/author?name=${encodeURIComponent(displayAuthor)}`;

  // The actionable region (library download/email, Listen Now, request /
  // download buttons) is rendered once near the top of the hero and, on mobile
  // only, duplicated at the very bottom so it's reachable after scrolling past
  // the description. Extracted into helpers so the conditionals live in one
  // place.
  const hasLibraryCard = Boolean(
    calibre && (calibreEbookFormats.length > 0 || calibreAudioFormats.length > 0),
  );
  const hasListenCard = Boolean(listenNowUrl);
  const hasAnyActionButton = Boolean(
    (canDownload && hasMissingFormat) ||
      (!hasAnyRequests && hasMissingFormat && canRequestAnything) ||
      (hasAnyRequests && !ebookAvailable && !audiobookAvailable) ||
      (hasAnyRequests && canClearRequests),
  );
  const hasActionRegion =
    hasLibraryCard || hasListenCard || hasAnyActionButton || Boolean(book.hardcoverSlug);

  // The "eBook Available" / "Audiobook Available" status is folded into the
  // library / Listen cards as their heading when those cards are present, so we
  // only show a standalone badge when there's no card to absorb it.
  const showEbookBadge = ebookAvailable && !hasLibraryCard;
  const showAudiobookBadge = audiobookAvailable && !hasListenCard;

  const renderEbookClearButton = () =>
    isAdmin && dbBook?.id && dbBook.ebook_available ? (
      <button
        onClick={() => {
          if (
            window.confirm('Clear eBook availability? This will allow you to re-download this book.')
          ) {
            clearAvailabilityMutation.mutate({ bookId: dbBook.id, formatType: 'ebook' });
          }
        }}
        className="ml-1 opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-emerald-500/20 rounded-lg"
        title="Clear eBook availability"
      >
        <X className="h-3 w-3 text-emerald-400" />
      </button>
    ) : null;

  const renderAudiobookClearButton = () =>
    isAdmin && dbBook?.id && dbBook.audiobook_available ? (
      <button
        onClick={() => {
          if (
            window.confirm(
              'Clear audiobook availability? This will allow you to re-download this book.',
            )
          ) {
            clearAvailabilityMutation.mutate({ bookId: dbBook.id, formatType: 'audiobook' });
          }
        }}
        className="ml-1 opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-violet-500/20 rounded-lg"
        title="Clear audiobook availability"
      >
        <X className="h-3 w-3 text-violet-400" />
      </button>
    ) : null;

  const renderLibraryCard = () => {
    if (!hasLibraryCard || !calibre) return null;
    return (
      <div className="rounded-xl md:rounded-2xl border border-border/50 bg-card/30 p-4 md:p-5 space-y-4">
        <div className="group flex items-center gap-2">
          {ebookAvailable ? (
            <>
              <BookOpen className="h-4 w-4 text-emerald-400" />
              <h2 className="text-sm font-semibold text-emerald-400">eBook Available</h2>
              {renderEbookClearButton()}
            </>
          ) : (
            <>
              <Library className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">In your library</h2>
            </>
          )}
          {isAdmin && (
            <span className="ml-auto flex flex-wrap gap-1">
              <Button
                variant="ghost"
                size="sm"
                disabled={calibreLinkMutation.isPending}
                onClick={() => calibreLinkMutation.mutate('refresh')}
              >
                Refresh
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={calibreLinkMutation.isPending}
                onClick={() => setSourcesOpen(true)}
              >
                Choose source
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={calibreLinkMutation.isPending}
                onClick={() => setRelinkOpen(true)}
              >
                Change
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={calibreLinkMutation.isPending}
                onClick={() => calibreLinkMutation.mutate('unlink')}
              >
                Unlink
              </Button>
            </span>
          )}
        </div>
        {calibreEbookFormats.length > 0 && (
          <CalibreFormatActions
            calibreBookId={calibre.calibre_book_id}
            formats={calibreEbookFormats}
            heading={calibreAudioFormats.length > 0 ? 'eBook' : null}
          />
        )}
        {calibreAudioFormats.length > 0 && (
          <CalibreFormatActions
            calibreBookId={calibre.calibre_book_id}
            formats={calibreAudioFormats}
            heading={calibreEbookFormats.length > 0 ? 'Audiobook' : null}
          />
        )}
      </div>
    );
  };

  const handleAudiobookDownload = async () => {
    if (!dbBook?.audiobookshelf_id) return;
    setDownloadingAudiobook(true);
    try {
      await audiobookshelfApi.downloadItem(dbBook.audiobookshelf_id);
    } catch (error) {
      toast.error('Audiobook download failed', {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setDownloadingAudiobook(false);
    }
  };

  const renderListenCard = () => {
    if (!listenNowUrl) return null;
    return (
      <div className="rounded-xl md:rounded-2xl border border-border/50 bg-card/30 p-4 md:p-5 space-y-3">
        <div className="group flex items-center gap-2">
          <Headphones className="h-4 w-4 text-violet-400" />
          {audiobookAvailable ? (
            <>
              <h2 className="text-sm font-semibold text-violet-400">Audiobook Available</h2>
              {renderAudiobookClearButton()}
            </>
          ) : (
            <h2 className="text-sm font-semibold text-foreground">
              {hasLibraryCard ? 'Listen on Audiobookshelf' : 'In your library'}
            </h2>
          )}
          {isAdmin && dbBook?.audiobookshelf_id && (
            <span className="ml-auto flex flex-wrap gap-1">
              <Button
                variant="ghost"
                size="sm"
                disabled={absLinkMutation.isPending}
                onClick={() => setAbsRelinkOpen(true)}
              >
                Change
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={absLinkMutation.isPending}
                onClick={() => absLinkMutation.mutate('unlink')}
              >
                Unlink
              </Button>
            </span>
          )}
        </div>
        <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
          <Button
            asChild
            className="w-full sm:w-auto h-11 px-5 rounded-xl bg-violet-600 hover:bg-violet-500 text-white font-medium shadow-lg shadow-violet-600/25 transition-[background-color,box-shadow] duration-300 hover:shadow-violet-500/40"
          >
            <a href={listenNowUrl} target="_blank" rel="noopener noreferrer">
              <Headphones className="h-4 w-4 mr-2" />
              Listen Now
            </a>
          </Button>
          {dbBook?.audiobookshelf_id && (
            <Button
              variant="outline"
              className="w-full sm:w-auto h-11 px-5 rounded-xl"
              disabled={downloadingAudiobook}
              title="Download the audiobook files (a zip when there are several)"
              onClick={handleAudiobookDownload}
            >
              {downloadingAudiobook ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Download className="h-4 w-4 mr-2" />
              )}
              Download
            </Button>
          )}
        </div>
      </div>
    );
  };

  const renderHardcoverButton = (className: string) =>
    book.hardcoverSlug ? (
      <Button size="lg" variant="outline" asChild className={className}>
        <a
          href={`https://hardcover.app/books/${book.hardcoverSlug}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLink className="h-4 w-4 mr-2" />
          View on Hardcover
        </a>
      </Button>
    ) : null;

  const renderActionButtons = (opts?: { hardcoverClassName?: string }) => (
    <>
      {/* Prowlarr Download button - show when user can download */}
      {canDownload && hasMissingFormat && (
        <Button
          size="lg"
          variant="default"
          onClick={() => {
            setSearchFormat(preferredFormat || 'ebook');
            setSearchSource('prowlarr');
            setSearchOpen(true);
          }}
          className="w-full sm:w-auto h-12 px-6 rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground font-medium shadow-lg shadow-primary/25 transition-[background-color,box-shadow] duration-300 hover:shadow-primary/40"
        >
          <Search className="h-4 w-4 mr-2" />
          Search Prowlarr
        </Button>
      )}

      {/* Request button - show if no downloads in progress and can request */}
      {!hasAnyRequests && hasMissingFormat && canRequestAnything && (
        <Button
          size="lg"
          variant="outline"
          onClick={() => setRequestOpen(true)}
          className="w-full sm:w-auto h-12 px-6 rounded-xl border-border/50 hover:bg-card hover:border-primary/30"
        >
          <Clock className="h-4 w-4 mr-2" />
          {preferredFormat === 'ebook'
            ? 'Request eBook'
            : preferredFormat === 'audiobook'
              ? 'Request Audiobook'
              : 'Request Book'}
        </Button>
      )}

      {/* Processing indicator */}
      {hasAnyRequests && !ebookAvailable && !audiobookAvailable && (
        <div className="flex w-full sm:w-auto items-center justify-center sm:justify-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-5 py-3 text-sm font-medium text-amber-400">
          <Clock className="h-4 w-4" />
          Requested • Processing
        </div>
      )}

      {opts?.hardcoverClassName ? renderHardcoverButton(opts.hardcoverClassName) : null}

      {hasAnyRequests && canClearRequests && (
        <Button
          size="lg"
          variant="outline"
          onClick={() => clearRequestsMutation.mutate()}
          disabled={clearRequestsMutation.isPending}
          className="w-full sm:w-auto h-12 px-6 rounded-xl border-rose-500/30 text-rose-400 hover:bg-rose-500/10 hover:border-rose-500/50"
        >
          <Trash2 className="h-4 w-4 mr-2" />
          {clearRequestsMutation.isPending
            ? 'Clearing...'
            : isAdmin
              ? 'Clear Request'
              : 'Cancel My Request'}
        </Button>
      )}
    </>
  );

  return (
    <>
      <div className="space-y-8 md:space-y-10 animate-fade-in-up">
        {/* Back Button — mirrors the Esc-to-go-back behaviour */}
        {(() => {
          const backState = location.state as { from?: string; fromLabel?: string } | null;
          const label =
            backState?.fromLabel ??
            (location.key === 'default' && !backState?.from ? 'Back to Discover' : 'Back');
          return (
            <button
              type="button"
              onClick={goBack}
              className="inline-flex items-center gap-2 text-muted-foreground hover:text-primary transition-colors duration-300 group"
            >
              <ArrowLeft className="h-4 w-4 transition-transform duration-300 group-hover:-translate-x-1" />
              <span className="font-medium">{label}</span>
            </button>
          );
        })()}

        {/* Hero Section */}
        <div className="relative rounded-2xl md:rounded-3xl overflow-hidden border border-border/40 md:border-0 bg-card/20 md:bg-transparent">
          {/* Blurred cover background – hidden on mobile for performance */}
          <div className="absolute inset-0 hidden md:block">
            <img
              src={displayCover}
              alt=""
              className="h-full w-full object-cover opacity-20 blur-3xl scale-125"
            />
            <div className="absolute inset-0 bg-gradient-to-r from-background via-background/95 to-background/80" />
            <div className="absolute inset-0 bg-gradient-to-t from-background via-transparent to-background/50" />
          </div>

          {/* Content */}
          <div className="relative flex flex-col gap-6 p-4 sm:p-6 md:flex-row md:gap-10 lg:gap-12 md:p-10">
            {/* Cover — paired with a compact identity block on mobile so the
                title/author/rating sit beside the cover instead of stacking
                below a full-width poster. On md the identity moves into the
                info column (see the `md:contents` block below). */}
            <div className="flex flex-row items-start gap-4 sm:gap-5 md:block md:flex-shrink-0">
              <div className="book-cover-glow shrink-0">
                <div className="book-cover w-[7.5rem] sm:w-40 md:w-64 aspect-[2/3]">
                  <img
                    src={displayCover}
                    alt={displayTitle}
                    className="h-full w-full object-cover"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src = '/placeholder.svg';
                    }}
                  />
                </div>
              </div>

              {/* Compact identity — mobile only */}
              <div className="flex min-w-0 flex-1 flex-col gap-1.5 pt-0.5 md:hidden">
                {renderSeries('text-xs font-medium')}
                <h1 className="text-xl font-bold leading-snug tracking-tight text-foreground text-balance">
                  {displayTitle}
                </h1>
                <Link
                  to={authorHref}
                  className="w-fit text-sm font-medium text-primary hover:underline underline-offset-4"
                >
                  {displayAuthor}
                </Link>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1 font-semibold text-foreground">
                    <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                    {formatRating(displayRating)}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Calendar className="h-3.5 w-3.5" />
                    {formatDate(displayPublishedDate)}
                  </span>
                  {displayPageCount > 0 && (
                    <span className="inline-flex items-center gap-1">
                      <BookOpen className="h-3.5 w-3.5" />
                      {displayPageCount} pages
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Info.
                On mobile this is an explicit flex column so `order-*` can float
                the availability/library/action region up directly under the
                compact header — genres and the (often long) description get
                `order-last` and drop below it. `md:order-none` restores the
                natural reading order on larger screens. */}
            <div className="flex-1 flex flex-col gap-5 text-left">
              {/* Identity — desktop only; `md:contents` keeps these as direct
                  flex children of the info column so the column gap applies. */}
              <div className="hidden md:contents">
                {renderSeries('w-fit text-sm font-medium')}
                <h1 className="text-4xl lg:text-5xl font-bold text-foreground tracking-tight leading-tight">
                  {displayTitle}
                </h1>
                <Link
                  to={authorHref}
                  className="w-fit text-xl text-primary font-medium hover:underline underline-offset-4 transition-colors"
                >
                  {displayAuthor}
                </Link>
              </div>

              {/* Meta info — desktop only; the mobile header carries a compact
                  version above. */}
              <div className="hidden md:flex flex-wrap gap-5 text-sm">
                <div className="flex items-center gap-2">
                  <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                  <span className="font-semibold text-foreground">{formatRating(displayRating)}</span>
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Calendar className="h-4 w-4" />
                  <span>{formatDate(displayPublishedDate)}</span>
                </div>
                {displayPageCount > 0 && (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <BookOpen className="h-4 w-4" />
                    <span>{displayPageCount} pages</span>
                  </div>
                )}
                {book.isbn && (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <span className="text-xs font-mono">ISBN: {book.isbn}</span>
                  </div>
                )}
              </div>

              {/* Genres */}
              {displayGenres.length > 0 && (
                <div className="order-last md:order-none flex flex-wrap gap-2">
                  {displayGenres.map((genre) => (
                    <Badge
                      key={genre}
                      variant="secondary"
                      className="px-2.5 py-0.5 rounded-md bg-muted/50 border-border/50 text-xs text-muted-foreground font-medium"
                    >
                      {genre}
                    </Badge>
                  ))}
                </div>
              )}

              {/* Description */}
              <div className="order-last md:order-none max-w-2xl">
                <h2 className="text-base md:text-lg font-semibold text-foreground mb-2 md:mb-3">Description</h2>
                <p className="text-sm md:text-base text-muted-foreground leading-relaxed">
                  {cleanDescription(displayDescription)}
                </p>
              </div>

              {/* Availability badges — only shown standalone here when there's
                  no library / Listen card to carry the status as its heading. */}
              {(showEbookBadge || showAudiobookBadge) && (
                <div className="flex flex-wrap gap-2">
                  {showEbookBadge && (
                    <div className="group flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                      <BookOpen className="h-3.5 w-3.5 text-emerald-400" />
                      <span className="text-xs md:text-sm font-medium text-emerald-400">eBook Available</span>
                      {renderEbookClearButton()}
                    </div>
                  )}
                  {showAudiobookBadge && (
                    <div className="group flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-500/10 border border-violet-500/30">
                      <Headphones className="h-3.5 w-3.5 text-violet-400" />
                      <span className="text-xs md:text-sm font-medium text-violet-400">Audiobook Available</span>
                      {renderAudiobookClearButton()}
                    </div>
                  )}
                </div>
              )}

              {/* Actionable region — library download/email, Listen Now and the
                  request/download buttons. Rendered here near the top; on mobile
                  it's duplicated at the bottom of the page (block below). */}
              {renderLibraryCard()}
              {renderListenCard()}

              {(hasAnyActionButton || book.hardcoverSlug) && (
                <div className="grid grid-cols-1 gap-2.5 pt-1 sm:flex sm:flex-wrap sm:gap-3 sm:pt-4">
                  {/* On mobile "View on Hardcover" is not shown here — it lives
                      in the bottom block instead. */}
                  {renderActionButtons({
                    hardcoverClassName:
                      'hidden md:inline-flex w-full sm:w-auto h-12 px-6 rounded-xl border-border/50 hover:bg-card hover:border-primary/30',
                  })}
                </div>
              )}

              {/* Mobile only — the same actions again after the description so
                  they're in reach without scrolling back up. `order-last` keeps
                  it below the description; `md:hidden` means desktop never
                  duplicates. */}
              {hasActionRegion && (
                <div className="order-last md:hidden flex flex-col gap-4 pt-5 border-t border-border/40">
                  {renderLibraryCard()}
                  {renderListenCard()}
                  {(hasAnyActionButton || book.hardcoverSlug) && (
                    <div className="grid grid-cols-1 gap-2.5">
                      {renderActionButtons({
                        hardcoverClassName:
                          'w-full h-12 px-6 rounded-xl border-border/50 hover:bg-card hover:border-primary/30',
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Hardcover Prompts */}
        {promptSummaries.length > 0 && (
          <section className="space-y-6">
            <div>
              <h2 className="text-2xl font-bold text-foreground tracking-tight">
                Hardcover Prompts
              </h2>
              <p className="text-sm text-muted-foreground mt-2">
                Prompts this book appears in, with top picks from each.
              </p>
            </div>
            <div className="space-y-6">
              {promptSummaries.map((summary, index) => {
                const prompt = summary.prompt;
                const promptBooks = (prompt?.prompt_books || [])
                  .map((entry: any) => entry?.book ? transformHardcoverBook(entry.book) : null)
                  .filter((book): book is ReturnType<typeof transformHardcoverBook> => Boolean(book));
                const defaultVisible = Math.min(10, promptBooks.length);
                const visibleBooks = promptBooks.slice(0, defaultVisible);
                const promptKey = String(prompt?.id ?? prompt?.slug ?? index);

                return (
                  <div
                    key={promptKey}
                    className="rounded-2xl border border-border/50 bg-card/30 p-6 animate-fade-in-up"
                    style={{ animationDelay: `${index * 100}ms` }}
                  >
                    <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                      <div className="space-y-2 flex-1">
                        <p className="text-xs text-primary uppercase tracking-wider font-medium">
                          Hardcover Prompt
                        </p>
                        <h3 className="text-xl font-semibold text-foreground">
                          {prompt?.question || 'Discover similar reads'}
                        </h3>
                        {prompt?.description && (
                          <p className="text-sm text-muted-foreground line-clamp-2">
                            {prompt.description}
                          </p>
                        )}
                        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                          {prompt?.answers_count != null && (
                            <span>{prompt.answers_count} answers</span>
                          )}
                          {prompt?.books_count != null && (
                            <span>{prompt.books_count} books</span>
                          )}
                          {prompt?.users_count != null && (
                            <span>{prompt.users_count} readers</span>
                          )}
                        </div>
                      </div>
                      {prompt?.slug && promptBooks.length > defaultVisible && (
                        <Link to={`/prompt/${prompt.slug}`}>
                          <Button variant="outline" size="sm" className="rounded-lg">
                            {prompt.books_count && prompt.books_count > promptBooks.length
                              ? `View More (${prompt.books_count} total)`
                              : `View All ${promptBooks.length} Books`}
                          </Button>
                        </Link>
                      )}
                    </div>
                    {visibleBooks.length > 0 && (
                      <div className="mt-6 flex gap-4 overflow-x-auto pb-4 scrollbar-hide -mx-2 px-2">
                        {visibleBooks.map((promptBook) => (
                          <div
                            key={promptBook.id}
                            className="flex-shrink-0 w-[130px] sm:w-[150px]"
                          >
                            <BookCard book={promptBook} showRating={false} showRequestButton={false} />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        )}
      </div>

      {calibre && (
        <CalibreRelinkDialog
          calibreId={calibre.calibre_book_id}
          open={relinkOpen}
          onOpenChange={setRelinkOpen}
          onLinked={invalidateCalibre}
        />
      )}

      {hasHardcoverId && dbBook?.audiobookshelf_id && (
        <AudiobookshelfRelinkDialog
          fromHardcoverId={hardcoverId as number}
          open={absRelinkOpen}
          onOpenChange={setAbsRelinkOpen}
          onLinked={invalidateAbs}
        />
      )}

      {calibre && isAdmin && (
        <MetadataSourceDialog
          calibreId={calibre.calibre_book_id}
          open={sourcesOpen}
          onOpenChange={setSourcesOpen}
          onApplied={invalidateCalibre}
        />
      )}

      <RequestDialog
        book={{ ...book, title: displayTitle, author: displayAuthor }}
        open={requestOpen}
        onOpenChange={setRequestOpen}
        preferredFormat={preferredFormat}
        disableFormats={{
          ebook: ebookAvailable,
          audiobook: audiobookAvailable,
        }}
      />

      {searchOpen && (
        <SearchReleaseDialog
          book={{
            id: dbBook?.id ?? requestStatus?.book_id ?? undefined,
            hardcoverId: hardcoverId,
            title: displayTitle,
            author: displayAuthor,
            isbn: book.isbn,
            description: displayDescription,
            cover: displayCover,
            publishedDate: displayPublishedDate,
            rating: displayRating,
            pageCount: displayPageCount,
            series: displaySeries,
            seriesPosition: displaySeriesPosition,
            genres: displayGenres,
          }}
          open={searchOpen}
          onOpenChange={setSearchOpen}
          formatType={searchFormat}
          sourceFilter={searchSource}
        />
      )}
    </>
  );
}
