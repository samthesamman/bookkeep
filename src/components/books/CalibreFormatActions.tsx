import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Download, Loader2, Mail } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { calibreApi } from '@/lib/api';
import { useUser } from '@/contexts/UserContext';

export interface CalibreFormat {
  format: string;
  size: number | null;
  name: string;
}

/**
 * Download / "email to myself" buttons for a book that lives in the Calibre
 * library. Shared by the My Books sheet and the book detail page.
 */
export function CalibreFormatActions({
  calibreBookId,
  formats,
  heading = 'Download',
}: {
  calibreBookId: number;
  formats: CalibreFormat[];
  heading?: string | null;
}) {
  const [downloading, setDownloading] = useState<string | null>(null);
  const [emailing, setEmailing] = useState<string | null>(null);
  const { user } = useUser();
  const deliveryEmail = user?.book_delivery_email || '';

  if (formats.length === 0) return null;

  // Only EPUB can be emailed, and only ever one button regardless of how many
  // formats exist. If there's no EPUB, no email button is shown.
  const emailFormat = formats.find((fmt) => fmt.format.toUpperCase() === 'EPUB');

  const handleDownload = async (format: string, name: string) => {
    setDownloading(format);
    try {
      await calibreApi.downloadFormat(calibreBookId, format, `${name}.${format.toLowerCase()}`);
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
      const result = await calibreApi.emailBook(calibreBookId, format);
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
    <div className="space-y-2">
      {heading && <h3 className="text-sm font-semibold text-foreground">{heading}</h3>}
      <div className="space-y-2">
        {formats.map((fmt) => (
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
          </div>
        ))}
        {emailFormat && (
          <Button
            variant="ghost"
            size="sm"
            disabled={emailing !== null || !deliveryEmail}
            title={
              deliveryEmail
                ? `Email this ${emailFormat.format} to ${deliveryEmail}`
                : 'Set a delivery email under Settings first'
            }
            onClick={() => handleEmail(emailFormat.format)}
          >
            {emailing === emailFormat.format ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Mail className="mr-2 h-4 w-4" />
            )}
            Email to myself
          </Button>
        )}
      </div>
      {!deliveryEmail && (
        <p className="text-xs text-muted-foreground">
          Add a delivery email under{' '}
          <Link to="/settings" className="text-primary underline">
            Settings
          </Link>{' '}
          to email books to yourself.
        </p>
      )}
    </div>
  );
}
