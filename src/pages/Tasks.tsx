import { useQuery } from '@tanstack/react-query';
import { CheckCircle, XCircle, Mail, Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { emailsApi } from '@/lib/api';

function formatDate(value: string | null): string {
  if (!value) return '—';
  const iso = value.endsWith('Z') || value.includes('+') ? value : `${value}Z`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

export default function Tasks() {
  const { data: emails = [], isLoading, isError, error } = useQuery({
    queryKey: ['email-logs'],
    queryFn: () => emailsApi.getAll(),
    retry: false,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
          <Mail className="h-6 w-6 text-primary" />
          Tasks
        </h1>
        <p className="mt-1 text-muted-foreground">
          Books you have emailed to yourself, and whether each send succeeded.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : isError ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-500">
          {error instanceof Error ? error.message : 'Failed to load your email history.'}
        </div>
      ) : emails.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border py-16 text-center">
          <Mail className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
          <h3 className="text-lg font-medium text-foreground">No emails sent yet</h3>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            When you email a book to yourself from My Books, it will show up here.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-foreground">Book</TableHead>
                <TableHead className="text-foreground">Format</TableHead>
                <TableHead className="text-foreground">Recipient</TableHead>
                <TableHead className="text-foreground">Sent</TableHead>
                <TableHead className="text-foreground">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {emails.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium text-foreground">
                    {row.book_title || row.subject || '—'}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{row.book_format || '—'}</TableCell>
                  <TableCell className="text-muted-foreground">{row.recipient}</TableCell>
                  <TableCell className="text-muted-foreground">{formatDate(row.created_at)}</TableCell>
                  <TableCell>
                    {row.status === 'success' ? (
                      <Badge className="gap-1 bg-green-500/15 text-green-500 hover:bg-green-500/15">
                        <CheckCircle className="h-3.5 w-3.5" />
                        Sent
                      </Badge>
                    ) : (
                      <Badge
                        variant="destructive"
                        className="gap-1"
                        title={row.error_message || undefined}
                      >
                        <XCircle className="h-3.5 w-3.5" />
                        Failed
                      </Badge>
                    )}
                    {row.status !== 'success' && row.error_message && (
                      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
                        {row.error_message}
                      </p>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
