import { useState } from 'react';
import { ChevronLeft, ChevronRight, Link2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { calibreApi, type MissingHardcoverBook } from '@/lib/api';
import { CalibreRelinkDialog } from '@/components/books/CalibreRelinkDialog';

const PAGE_SIZE = 50;

export default function MissingHardcoverBooks() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [linking, setLinking] = useState<MissingHardcoverBook | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['calibre', 'missing-hardcover', page],
    queryFn: () => calibreApi.getMissingHardcoverBooks(page, PAGE_SIZE),
  });

  const books = data?.books ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const rangeStart = data && data.total > 0 ? (page - 1) * PAGE_SIZE + 1 : 0;
  const rangeEnd = data ? Math.min(page * PAGE_SIZE, data.total) : 0;

  const handleLinked = () => {
    queryClient.invalidateQueries({ queryKey: ['calibre', 'missing-hardcover'] });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Missing Hardcover Links</h1>
        <p className="text-muted-foreground mt-1">
          Ebooks in your Calibre library that couldn't be automatically matched to a Hardcover
          book. Search and link each one manually, or leave it - automated syncs won't touch
          these until you do.
        </p>
      </div>

      <div className="bg-card border border-border rounded-lg">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-foreground">Cover</TableHead>
              <TableHead className="text-foreground">Title</TableHead>
              <TableHead className="text-foreground">Author</TableHead>
              <TableHead className="text-foreground">ISBN</TableHead>
              <TableHead className="text-right text-foreground">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : error ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-destructive">
                  Error loading books: {error instanceof Error ? error.message : 'Unknown error'}
                </TableCell>
              </TableRow>
            ) : books.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  Every Calibre-linked ebook has a Hardcover match. Nothing to do here.
                </TableCell>
              </TableRow>
            ) : (
              books.map((book) => (
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
                  <TableCell className="text-muted-foreground">{book.isbn || '—'}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-2"
                      onClick={() => setLinking(book)}
                    >
                      <Link2 className="h-4 w-4" />
                      Link to Hardcover
                    </Button>
                  </TableCell>
                </TableRow>
              ))
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
        <CalibreRelinkDialog
          calibreId={linking.calibre_book_id}
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
