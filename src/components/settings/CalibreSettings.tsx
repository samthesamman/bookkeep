import { useState, useEffect } from 'react';
import { Save, TestTube, CheckCircle, XCircle, Library, Server, Eye, EyeOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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

  const [agentEnabled, setAgentEnabled] = useState(false);
  const [agentUrl, setAgentUrl] = useState('');
  const [agentApiKey, setAgentApiKey] = useState('');
  const [showAgentApiKey, setShowAgentApiKey] = useState(false);
  const [agentConvertFormat, setAgentConvertFormat] = useState('');
  const [agentTesting, setAgentTesting] = useState(false);
  const [agentTestResult, setAgentTestResult] = useState<{ success: boolean; message: string } | null>(null);

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
      setAgentEnabled(settings.agent_enabled);
      setAgentUrl(settings.agent_url || '');
      setAgentApiKey(settings.agent_api_key || '');
      setAgentConvertFormat(settings.agent_convert_format || '');
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
      calibreApi.updateSettings({
        library_path: libraryPath.trim() || null,
        enabled,
        agent_enabled: agentEnabled,
        agent_url: agentUrl.trim() || null,
        agent_api_key: agentApiKey.trim() || null,
        agent_convert_format: agentConvertFormat.trim() || null,
      }),
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

  const handleTestAgent = async () => {
    if (!agentUrl.trim() || !agentApiKey.trim()) {
      toast.error('Enter the agent URL and API key first');
      return;
    }
    setAgentTesting(true);
    setAgentTestResult(null);
    try {
      const result = await calibreApi.testAgent(agentUrl.trim(), agentApiKey.trim());
      if (result.success) {
        setAgentTestResult({ success: true, message: 'Connected' });
        toast.success('Connected to calibre-cli');
      } else {
        const msg = result.error || 'Could not reach the calibre-cli';
        setAgentTestResult({ success: false, message: msg });
        toast.error(msg);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Test failed';
      setAgentTestResult({ success: false, message: msg });
      toast.error('Test failed', { description: msg });
    } finally {
      setAgentTesting(false);
    }
  };

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
                Point Bookworms at a Calibre library directory to browse it on the "My Books" page.
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
            readable by the Bookworms backend (mount it into the container if you run in Docker).
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

        <div className="space-y-4 rounded-lg border border-border p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-muted-foreground" />
              <div className="space-y-1">
                <Label className="text-foreground font-medium">Calibre server</Label>
                <p className="text-sm text-muted-foreground">
                  Optional. Push bookkeep's metadata and cover into your real Calibre library via a{' '}
                  <span className="font-mono text-xs">calibre-cli</span> companion service, then
                  optionally re-embed that metadata into the book file itself. Calibre stays untouched
                  if this is left off.
                </p>
              </div>
            </div>
            <Switch checked={agentEnabled} onCheckedChange={setAgentEnabled} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="calibre-cli-url" className="text-foreground">
                Agent URL
              </Label>
              <Input
                id="calibre-cli-url"
                value={agentUrl}
                onChange={(e) => setAgentUrl(e.target.value)}
                placeholder="http://calibre-cli.local:8100"
                disabled={!agentEnabled}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="calibre-cli-key" className="text-foreground">
                Agent API Key
              </Label>
              <div className="relative">
                <Input
                  id="calibre-cli-key"
                  type={showAgentApiKey ? 'text' : 'password'}
                  value={agentApiKey}
                  onChange={(e) => setAgentApiKey(e.target.value)}
                  placeholder="shared secret"
                  disabled={!agentEnabled}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowAgentApiKey((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  tabIndex={-1}
                >
                  {showAgentApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="calibre-cli-convert" className="text-foreground">
              Re-embed metadata into file format after update (optional)
            </Label>
            <Input
              id="calibre-cli-convert"
              value={agentConvertFormat}
              onChange={(e) => setAgentConvertFormat(e.target.value)}
              placeholder="e.g. epub"
              disabled={!agentEnabled}
              className="max-w-[200px]"
            />
            <p className="text-xs text-muted-foreground">
              Bakes the updated title/author/cover/etc. directly into the book file of this format
              (calibredb embed_metadata) — always runs, even if the book already has this format.
              Leave blank to only push metadata/cover to Calibre and skip this step.
            </p>
          </div>

          {agentTestResult && (
            <div
              className={`flex items-center gap-2 text-sm p-3 rounded-lg ${
                agentTestResult.success
                  ? 'bg-green-500/10 border border-green-500/30 text-green-500'
                  : 'bg-red-500/10 border border-red-500/30 text-red-500'
              }`}
            >
              {agentTestResult.success ? (
                <CheckCircle className="h-4 w-4" />
              ) : (
                <XCircle className="h-4 w-4" />
              )}
              <span>{agentTestResult.message}</span>
            </div>
          )}

          <Button
            variant="outline"
            onClick={handleTestAgent}
            disabled={agentTesting || !agentEnabled || !agentUrl.trim() || !agentApiKey.trim()}
          >
            <TestTube className="h-4 w-4 mr-2" />
            {agentTesting ? 'Testing...' : 'Test Connection'}
          </Button>
        </div>

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
