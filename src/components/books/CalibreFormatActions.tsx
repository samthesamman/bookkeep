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
 * Download / "Send to Device" buttons for a book that lives in the Calibre
 * library. Shared by the My Books sheet and the book detail page. Only EPUB
 * can be sent to a device, and only one such button is ever shown.
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
        {emailFormat && (
          <Button
            disabled={emailing !== null || !deliveryEmail}
            title={
              deliveryEmail
                ? `Send this ${emailFormat.format} to ${deliveryEmail}`
                : 'Set a delivery email under Settings first'
            }
            onClick={() => handleEmail(emailFormat.format)}
            className="w-full sm:w-auto h-11 px-5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-lg shadow-emerald-600/25 transition-[background-color,box-shadow] duration-300 hover:shadow-emerald-500/40"
          >
            {emailing === emailFormat.format ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Mail className="mr-2 h-4 w-4" />
            )}
            Send to Device
          </Button>
        )}
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
      </div>
      {emailFormat && !deliveryEmail && (
        <p className="text-xs text-muted-foreground">
          Add a delivery email under{' '}
          <Link to="/settings" className="text-primary underline">
            Settings
          </Link>{' '}
          to send books to your device.
        </p>
      )}
    </div>
  );
}
