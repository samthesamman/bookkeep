import { useState, useEffect } from 'react';
import { Save, TestTube, CheckCircle, XCircle, Library } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { toast } from 'sonner';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { calibreApi } from '@/lib/api';
import DirectoryPicker from '@/components/settings/DirectoryPicker';

export default function CalibreSettings() {
  const queryClient = useQueryClient();
  const [libraryPath, setLibraryPath] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['calibre-settings'],
    queryFn: () => calibreApi.getSettings(),
  });

  const { data: overlay } = useQuery({
    queryKey: ['calibre-overlay-settings'],
    queryFn: () => calibreApi.getOverlaySettings(),
  });

  useEffect(() => {
    if (settings) {
      setLibraryPath(settings.library_path || '');
      setEnabled(settings.enabled);
    }
  }, [settings]);

  const overlayMutation = useMutation({
    mutationFn: (data: { enabled: boolean; prefer_local: boolean }) =>
      calibreApi.updateOverlaySettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calibre-overlay-settings'] });
      queryClient.invalidateQueries({ queryKey: ['calibre-books'] });
      toast.success('Metadata settings saved!');
    },
    onError: (error: Error) => {
      toast.error('Failed to save', { description: error.message });
    },
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      calibreApi.updateSettings({ library_path: libraryPath.trim() || null, enabled }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['calibre-settings'] });
      queryClient.invalidateQueries({ queryKey: ['calibre-books'] });
      if (data.enabled && !data.valid) {
        toast.warning('Saved, but the library could not be read', {
          description: data.error || 'Check that the path contains metadata.db',
        });
      } else {
        toast.success('Calibre settings saved!');
      }
    },
    onError: (error: Error) => {
      toast.error('Failed to save settings', { description: error.message });
    },
  });

  const handleTest = async () => {
    if (!libraryPath.trim()) {
      toast.error('Enter a library path first');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await calibreApi.test(libraryPath.trim());
      if (result.success) {
        const msg = `Found ${result.book_count ?? 0} books`;
        setTestResult({ success: true, message: msg });
        toast.success(msg);
      } else {
        const msg = result.error || 'Could not read the Calibre library';
        setTestResult({ success: false, message: msg });
        toast.error(msg);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Test failed';
      setTestResult({ success: false, message: msg });
      toast.error('Test failed', { description: msg });
    } finally {
      setTesting(false);
    }
  };

  if (isLoading) {
    return (
      <Card className="bg-card border-border">
        <CardContent className="p-6">
          <div className="animate-pulse space-y-4">
            <div className="h-4 bg-muted rounded w-1/4" />
            <div className="h-10 bg-muted rounded" />
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="bg-card border-border">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-500/10">
              <Library className="h-5 w-5 text-amber-500" />
            </div>
            <div>
              <CardTitle className="text-foreground">Calibre</CardTitle>
              <CardDescription>
                Point Bookkeep at a Calibre library directory to browse it on the "My Books" page.
              </CardDescription>
            </div>
          </div>
          {enabled && settings?.valid && (
            <Badge variant="outline" className="border-green-500/40 text-green-500">
              {settings.book_count ?? 0} books
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="flex items-center justify-between p-4 rounded-lg bg-secondary/30 border border-border">
          <div className="space-y-1">
            <Label className="text-foreground font-medium">Enable "My Books"</Label>
            <p className="text-sm text-muted-foreground">
              Show the Calibre library to all logged-in users
            </p>
          </div>
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </div>

        <div className="space-y-2">
          <Label htmlFor="calibre-path" className="text-foreground">
            Calibre Database Directory
          </Label>
          <DirectoryPicker
            id="calibre-path"
            value={libraryPath}
            onChange={setLibraryPath}
            placeholder="/path/to/Calibre Library"
          />
          <p className="text-xs text-muted-foreground">
            The folder that contains <code className="font-mono">metadata.db</code>. It must be
            readable by the Bookkeep backend (mount it into the container if you run in Docker).
          </p>
        </div>

        {overlay && (
          <div className="space-y-3 rounded-lg border border-border p-4">
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <Label className="text-foreground font-medium">
                  Enrich library metadata from Hardcover
                </Label>
                <p className="text-sm text-muted-foreground">
                  Overlay covers, descriptions, ratings, series and genres onto matched
                  books. Calibre is never modified.
                </p>
              </div>
              <Switch
                checked={overlay.enabled}
                onCheckedChange={(v) =>
                  overlayMutation.mutate({ enabled: v, prefer_local: overlay.prefer_local })
                }
              />
            </div>
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <Label className="text-foreground font-medium">
                  Prefer Hardcover metadata over Calibre's
                </Label>
                <p className="text-sm text-muted-foreground">
                  When off, Hardcover data only fills fields Calibre leaves empty.
                </p>
              </div>
              <Switch
                checked={overlay.prefer_local}
                disabled={!overlay.enabled}
                onCheckedChange={(v) =>
                  overlayMutation.mutate({ enabled: overlay.enabled, prefer_local: v })
                }
              />
            </div>
          </div>
        )}

        {testResult && (
          <div
            className={`flex items-center gap-2 text-sm p-3 rounded-lg ${
              testResult.success
                ? 'bg-green-500/10 border border-green-500/30 text-green-500'
                : 'bg-red-500/10 border border-red-500/30 text-red-500'
            }`}
          >
            {testResult.success ? (
              <CheckCircle className="h-4 w-4" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            <span>{testResult.message}</span>
          </div>
        )}

        <div className="flex gap-3 pt-2">
          <Button variant="outline" onClick={handleTest} disabled={testing || !libraryPath.trim()}>
            <TestTube className="h-4 w-4 mr-2" />
            {testing ? 'Testing...' : 'Test Connection'}
          </Button>
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            <Save className="h-4 w-4 mr-2" />
            {saveMutation.isPending ? 'Saving...' : 'Save Settings'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
