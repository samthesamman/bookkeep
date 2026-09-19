import { useState } from 'react';
import { ChevronLeft, ChevronRight, Link2, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { booksApi, type MissingMetadataBook } from '@/lib/api';
import { LinkHardcoverDialog } from '@/components/books/LinkHardcoverDialog';

const PAGE_SIZE = 50;

export default function MissingMetadataBooks() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [linking, setLinking] = useState<MissingMetadataBook | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['books', 'missing-metadata', page],
    queryFn: () => booksApi.getMissingMetadata(page, PAGE_SIZE),
  });

  const books = data?.books ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const rangeStart = data && data.total > 0 ? (page - 1) * PAGE_SIZE + 1 : 0;
  const rangeEnd = data ? Math.min(page * PAGE_SIZE, data.total) : 0;

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['books', 'missing-metadata'] });

  const retryMutation = useMutation({
    mutationFn: (bookId: number) => booksApi.retryMetadataSync(bookId),
    onSuccess: (result, bookId) => {
      const book = books.find((b) => b.book_id === bookId);
      if (result.found) {
        toast.success('Found new metadata', {
          description: book ? `"${book.title}" has been updated.` : undefined,
        });
      } else {
        toast.info('Still nothing new', {
          description: book
            ? `Every source responded, but "${book.title}" is unchanged.`
            : undefined,
        });
      }
      invalidate();
    },
    onError: (err) => {
      toast.error('Retry failed', {
        description: err instanceof Error ? err.message : 'Metadata sources unavailable — try again later.',
      });
    },
  });

  const handleLinked = () => {
    invalidate();
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Missing Metadata</h1>
        <p className="text-muted-foreground mt-1">
          Books the scheduled metadata syncs searched every source for and found nothing new.
          They're skipped on future runs until you retry or link one manually here.
        </p>
      </div>

      <div className="bg-card border border-border rounded-lg">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-foreground">Cover</TableHead>
              <TableHead className="text-foreground">Title</TableHead>
              <TableHead className="text-foreground">Author</TableHead>
              <TableHead className="text-foreground">Status</TableHead>
              <TableHead className="text-foreground">Last attempt</TableHead>
              <TableHead className="text-right text-foreground">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : error ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-destructive">
                  Error loading books: {error instanceof Error ? error.message : 'Unknown error'}
                </TableCell>
              </TableRow>
            ) : books.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  Every book is fully matched, or hasn't been searched yet. Nothing to do here.
                </TableCell>
              </TableRow>
            ) : (
              books.map((book) => {
                const isRetrying =
                  retryMutation.isPending && retryMutation.variables === book.book_id;
                return (
                  <TableRow key={book.book_id}>
                    <TableCell>
                      <img
                        src={book.cover_url || '/placeholder.svg'}
                        alt=""
                        className="h-16 w-11 rounded object-cover bg-secondary"
                      />
                    </TableCell>
                    <TableCell className="font-medium text-foreground">{book.title}</TableCell>
                    <TableCell className="text-muted-foreground">{book.author || '—'}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        <Badge variant={book.hardcover_id ? 'secondary' : 'outline'}>
                          {book.hardcover_id ? 'Matched, incomplete' : 'No Hardcover match'}
                        </Badge>
                        {book.calibre_linked && <Badge variant="outline">Calibre</Badge>}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {book.last_attempted_at
                        ? new Date(book.last_attempted_at).toLocaleDateString()
                        : '—'}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-2"
                          disabled={isRetrying}
                          onClick={() => retryMutation.mutate(book.book_id)}
                        >
                          <RotateCw className={`h-4 w-4 ${isRetrying ? 'animate-spin' : ''}`} />
                          Retry
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-2"
                          onClick={() => setLinking(book)}
                        >
                          <Link2 className="h-4 w-4" />
                          Link manually
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

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

      {linking && (
        <LinkHardcoverDialog
          bookId={linking.book_id}
          initialQuery={linking.title}
          open={true}
          onOpenChange={(open) => {
            if (!open) setLinking(null);
          }}
          onLinked={handleLinked}
        />
      )}
    </div>
  );
}
