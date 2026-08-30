// Use relative paths when served from same origin
const API_BASE_URL = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? '' : 'http://localhost:8000');

let backendAvailable: boolean | null = null;

// Token storage keys
const ACCESS_TOKEN_KEY = 'accessToken';
const REFRESH_TOKEN_KEY = 'refreshToken';

// Token management functions
export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  // Also clear legacy userId if present
  localStorage.removeItem('userId');
}

export function isAuthenticated(): boolean {
  return !!getAccessToken();
}

// Token refresh logic
let refreshPromise: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  // If already refreshing, wait for that promise
  if (refreshPromise) {
    return refreshPromise;
  }

  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return false;
  }

  refreshPromise = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (!response.ok) {
        clearTokens();
        return false;
      }

      const data = await response.json();
      localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
      return true;
    } catch {
      clearTokens();
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

export async function checkBackendAvailable(): Promise<boolean> {
  if (backendAvailable !== null) return backendAvailable;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    const response = await fetch(`${API_BASE_URL}/api/users/check/admin-exists`, {
      method: 'GET',
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!response.ok) {
      backendAvailable = false;
      return false;
    }

    const contentType = response.headers.get('content-type');
    if (!contentType || !contentType.includes('application/json')) {
      backendAvailable = false;
      return false;
    }

    const data = await response.json();
    backendAvailable = typeof data.admin_exists === 'boolean';
    return backendAvailable;
  } catch {
    backendAvailable = false;
    return false;
  }
}

export function isBackendAvailable(): boolean | null {
  return backendAvailable;
}

async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {},
  retry = true
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...options.headers as Record<string, string>,
  };

  // Add Authorization header if we have a token
  const accessToken = getAccessToken();
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  // Handle 401 - try to refresh token
  if (response.status === 401 && retry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      // Retry the request with the new token
      return apiRequest<T>(endpoint, options, false);
    }
    // Refresh failed - redirect to login
    window.location.href = '/login';
    throw new Error('Session expired');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Request failed' }));
    throw new Error(error.detail || error.message || `API request failed: ${response.status}`);
  }

  // Handle 204 No Content and other empty responses
  if (response.status === 204 || response.status === 205) {
    return undefined as T;
  }

  // Check if response has content
  const contentType = response.headers.get('content-type');
  if (!contentType || !contentType.includes('application/json')) {
    return undefined as T;
  }

  // Try to parse JSON, but handle empty body gracefully
  const text = await response.text();
  if (!text || text.trim() === '') {
    return undefined as T;
  }

  return JSON.parse(text);
}

// Hardcover API endpoints
export const hardcoverApi = {
  search: (query: string, limit: number = 20) =>
    apiRequest<{ books: any[] }>(`/api/hardcover/search?query=${encodeURIComponent(query)}&limit=${limit}`),

  searchGrouped: (
    query: string,
    limit: number = 10,
    options?: { cacheOnly?: boolean }
  ) => {
    const params = new URLSearchParams({
      query: query,
      limit: String(limit),
    });
    if (options?.cacheOnly) {
      params.set('cache_only', 'true');
    }
    return apiRequest<{ series: any[]; authors: any[]; books: any[] }>(
      `/api/hardcover/search-grouped?${params.toString()}`
    );
  },

  getDetails: (bookId: number, options?: { bypassCache?: boolean }) => {
    const params = new URLSearchParams();
    if (options?.bypassCache) {
      params.set('bypass_cache', 'true');
    }
    const query = params.toString();
    return apiRequest<{ books_by_pk: any }>(
      `/api/hardcover/details/${bookId}${query ? `?${query}` : ''}`
    );
  },

  getEditions: (bookId: number, format?: 'ebook' | 'audiobook') => {
    const query = format ? `?format=${format}` : '';
    return apiRequest<{
      default_cover_edition_id: number | null;
      default_ebook_edition_id: number | null;
      default_audio_edition_id: number | null;
      editions: Array<{
        id: number;
        title?: string;
        score?: number;
        reading_format_id?: number;
        reading_format?: string;
        language?: string;
        language_code2?: string;
        publisher?: string;
        pages?: number;
        audio_seconds?: number;
        edition_format?: string;
        release_date?: string;
        release_year?: number;
      }>;
    }>(`/api/hardcover/editions/${bookId}${query}`);
  },

  getTrending: (limit: number = 20) =>
    apiRequest<{ books: any[] }>(`/api/hardcover/trending?limit=${limit}`),

  getPopular: (limit: number = 20) =>
    apiRequest<{ books: any[] }>(`/api/hardcover/popular?limit=${limit}`),

  getNewReleases: (limit: number = 20, minRatings: number = 5) => {
    const params = new URLSearchParams({
      limit: String(limit),
      min_ratings: String(minRatings),
    });
    return apiRequest<{ books: any[] }>(`/api/hardcover/new-releases?${params}`);
  },

  getSeries: (seriesId: number, options?: { bypassCache?: boolean }) => {
    const params = new URLSearchParams();
    if (options?.bypassCache) {
      params.set('bypass_cache', 'true');
    }
    const query = params.toString();
    return apiRequest<{ series_by_pk: any }>(
      `/api/hardcover/series/${seriesId}${query ? `?${query}` : ''}`
    );
  },

  rebuildSeries: (seriesId: number) =>
    apiRequest<{ series_by_pk: any }>(`/api/hardcover/series/${seriesId}/rebuild`, {
      method: 'POST',
    }),

  getSimilar: (bookId: number, limit: number = 10) =>
    apiRequest<{ books: any[] }>(`/api/hardcover/similar/${bookId}?limit=${limit}`),

  getBookPrompts: (bookId: number, promptLimit: number = 6, booksLimit: number = 30) =>
    apiRequest<{ prompt_summaries: any[] }>(
      `/api/hardcover/prompts/${bookId}?prompt_limit=${promptLimit}&books_limit=${booksLimit}`
    ),

  getPromptBySlug: (slug: string, limit: number = 1000, offset: number = 0, bypassCache: boolean = false) =>
    apiRequest<{ prompt: any }>(
      `/api/hardcover/prompt/${encodeURIComponent(slug)}?limit=${limit}&offset=${offset}&bypass_cache=${bypassCache}`
    ),

  getByAuthor: (bookId: number, limit: number = 10) =>
    apiRequest<{ books: any[] }>(`/api/hardcover/by-author/${bookId}?limit=${limit}`),

  getPopularSeries: (limit: number = 20, minTotalRatings: number = 500, offset: number = 0) => {
    const params = new URLSearchParams({
      limit: String(limit),
      min_total_ratings: String(minTotalRatings),
    });
    if (offset > 0) params.set('offset', String(offset));
    return apiRequest<{ series: any[] }>(`/api/hardcover/popular-series?${params}`);
  },

  getAuthor: (
    name: string,
    options?: {
      books_limit?: number;
      books_offset?: number;
      series_limit?: number;
      series_offset?: number;
    }
  ) => {
    const params = new URLSearchParams({ name });
    if (options?.books_limit != null) params.set('books_limit', String(options.books_limit));
    if (options?.books_offset != null) params.set('books_offset', String(options.books_offset));
    if (options?.series_limit != null) params.set('series_limit', String(options.series_limit));
    if (options?.series_offset != null) params.set('series_offset', String(options.series_offset));
    return apiRequest<{
      author: any;
      books: any[];
      books_total: number;
      series: any[];
      series_total: number;
    }>(`/api/hardcover/author?${params.toString()}`);
  },
};

// Discover (NYT Best Sellers) API endpoints
export interface NytListName {
  list_name: string | null;
  list_name_encoded: string;
  display_name: string | null;
  updated: string | null;
  oldest_published_date: string | null;
  newest_published_date: string | null;
}

export interface NytBestsellerList {
  list_name: string;
  list_name_encoded: string;
  updated: string | null;
  books: import('@/lib/hardcover').HardcoverBook[];
}

export const discoverApi = {
  getStatus: () =>
    apiRequest<{ has_nyt_key: boolean }>('/api/discover/status'),

  getBestsellers: () =>
    apiRequest<{ lists: NytBestsellerList[]; attribution: string; generated_at: string | null }>(
      '/api/discover/bestsellers'
    ),

  getNytLists: () =>
    apiRequest<{ available: NytListName[]; selected: string[]; has_nyt_key: boolean }>(
      '/api/discover/nyt-lists'
    ),

  setNytLists: (lists: string[]) =>
    apiRequest<{ available: NytListName[]; selected: string[]; has_nyt_key: boolean }>(
      '/api/discover/nyt-lists',
      { method: 'PUT', body: JSON.stringify({ lists }) }
    ),
};

/** A locally-stored Book row (subset used by the detail pages). */
export interface LocalBook {
  id: number;
  title: string;
  author: string;
  isbn: string | null;
  description: string | null;
  cover_url: string | null;
  published_date: string | null;
  rating: number | null;
  page_count: number | null;
  hardcover_id: number | null;
  series: string | null;
  series_id: number | null;
  series_position: number | null;
  genres: string[] | null;
  ebook_available: boolean;
  audiobook_available: boolean;
  metadata_locked: boolean;
}

// Books API endpoints
export const booksApi = {
  getAll: (skip: number = 0, limit: number = 100) =>
    apiRequest<Array<any>>(`/api/books/?skip=${skip}&limit=${limit}`),

  getById: (id: number) =>
    apiRequest<any>(`/api/books/${id}`),

  /** Local Book row for a Hardcover id, or null if we have not saved it. */
  getByHardcoverId: async (hardcoverId: number): Promise<LocalBook | null> => {
    try {
      return await apiRequest<LocalBook>(`/api/books/by-hardcover/${hardcoverId}`);
    } catch {
      return null;
    }
  },

  create: (book: any) =>
    apiRequest<any>('/api/books/', {
      method: 'POST',
      body: JSON.stringify(book),
    }),

  update: (id: number, book: any) =>
    apiRequest<any>(`/api/books/${id}`, {
      method: 'PUT',
      body: JSON.stringify(book),
    }),

  delete: (id: number) =>
    apiRequest<void>(`/api/books/${id}`, {
      method: 'DELETE',
    }),

  clearAvailability: (id: number, formatType: 'ebook' | 'audiobook') =>
    apiRequest<{ success: boolean; book_id: number; format_type: string; ebook_available: boolean; audiobook_available: boolean }>(
      `/api/books/${id}/availability/${formatType}`,
      { method: 'DELETE' }
    ),
};

// Requests API endpoints
export const requestsApi = {
  getAll: (skip: number = 0, limit: number = 100, status?: string, userId?: number) => {
    const params = new URLSearchParams({
      skip: String(skip),
      limit: String(limit),
    });
    if (status) params.append('status_filter', status);
    if (userId) params.append('user_id', String(userId));
    return apiRequest<Array<any>>(`/api/requests/?${params}`);
  },

  getById: (id: number) =>
    apiRequest<any>(`/api/requests/${id}`),

  create: (request: { book_id: number; format: string; notes?: string; edition_id?: number; auto_email_when_available?: boolean }) =>
    apiRequest<any>('/api/requests/', {
      method: 'POST',
      body: JSON.stringify(request),
    }),

  update: (id: number, update: { status?: string; admin_notes?: string }) =>
    apiRequest<any>(`/api/requests/${id}`, {
      method: 'PUT',
      body: JSON.stringify(update),
    }),

  delete: (id: number) =>
    apiRequest<void>(`/api/requests/${id}`, {
      method: 'DELETE',
    }),

  // Admin: (re)create the Audiobook Media Path hardlink for a finished audiobook download.
  createHardlink: (id: number) =>
    apiRequest<{ success: boolean; path: string; status: string; message: string }>(
      `/api/requests/${id}/create-hardlink`,
      { method: 'POST' }
    ),

  getByBook: (bookId: number) =>
    apiRequest<{ ebook: string | null; audiobook: string | null }>(`/api/requests/by-book/${bookId}`),

  getByHardcoverId: (hardcoverId: number) =>
    apiRequest<{
      ebook: string | null;
      audiobook: string | null;
      ebook_readarr_book_id: number | null;
      audiobook_readarr_book_id: number | null;
      book_id: number | null;
      // Whether the current user personally has an active request for this format.
      ebook_mine?: boolean;
      audiobook_mine?: boolean;
    }>(`/api/requests/by-hardcover/${hardcoverId}`),

  clearByHardcoverId: (hardcoverId: number, format?: 'ebook' | 'audiobook') =>
    apiRequest<{ message: string; deleted_count: number; formats: string[] }>(
      `/api/requests/by-hardcover/${hardcoverId}${format ? `?format=${format}` : ''}`,
      { method: 'DELETE' }
    ),

  getByHardcoverBatch: (hardcoverIds: number[]) =>
    apiRequest<{ results: Array<{ hardcover_id: number; ebook: string | null; audiobook: string | null }> }>(
      '/api/requests/by-hardcover/batch',
      {
        method: 'POST',
        body: JSON.stringify({ hardcover_ids: hardcoverIds }),
      }
    ),

  requestSeries: (
    seriesId: number,
    format: 'ebook' | 'audiobook' = 'ebook',
    originalOnly: boolean = false
  ) =>
    apiRequest<{
      series_id: number;
      format: string;
      requested_count: number;
      skipped_count: number;
      already_available: number;
      already_requested: number;
      total_books: number;
    }>(`/api/requests/series/${seriesId}?format=${format}${originalOnly ? '&original_only=true' : ''}`, {
      method: 'POST',
    }),

  clearSeries: (seriesId: number, format?: 'ebook' | 'audiobook') =>
    apiRequest<{
      message: string;
      series_id: number;
      deleted_count: number;
      formats: string[];
      affected_books: number;
    }>(`/api/requests/series/${seriesId}${format ? `?format=${format}` : ''}`, {
      method: 'DELETE',
    }),
};

// User type for the API
export interface ApiUser {
  id: number;
  email: string;
  username: string;
  full_name?: string;
  is_active: boolean;
  is_admin: boolean;
  has_password: boolean;
  can_request_ebook: boolean;
  can_request_audiobook: boolean;
  can_download: boolean;
  auto_approve_ebooks: boolean;
  auto_approve_audiobooks: boolean;
  book_delivery_email?: string | null;
  created_at: string;
  updated_at?: string;
}

// Auth response types
export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface OidcSettingField {
  value: string | null;
  source: string;
}

export interface OidcSettingsResponse {
  enabled: boolean;
  oidc_issuer_url: OidcSettingField;
  oidc_client_id: OidcSettingField;
  oidc_client_secret: OidcSettingField;
  oidc_redirect_uri: OidcSettingField;
  oidc_auto_register: OidcSettingField;
  oidc_button_text: OidcSettingField;
}

// Auth API endpoints
export const authApi = {
  login: async (username: string, password: string): Promise<LoginResponse> => {
    const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Login failed' }));
      throw new Error(error.detail || 'Invalid username or password');
    }

    const data = await response.json();
    setTokens(data.access_token, data.refresh_token);
    return data;
  },

  refresh: () =>
    apiRequest<{ access_token: string; token_type: string; expires_in: number }>(
      '/api/auth/refresh',
      {
        method: 'POST',
        body: JSON.stringify({ refresh_token: getRefreshToken() }),
      }
    ),

  logout: () => {
    clearTokens();
  },

  getOidcConfig: async (): Promise<{ enabled: boolean; button_text: string; logout_url: string | null }> => {
    const response = await fetch(`${API_BASE_URL}/api/auth/oidc/config`);
    if (!response.ok) {
      return { enabled: false, button_text: 'Sign in with SSO', logout_url: null };
    }
    return response.json();
  },
};

// Users API endpoints
export const usersApi = {
  checkAdminExists: () =>
    apiRequest<{ admin_exists: boolean }>('/api/users/check/admin-exists'),

  getMe: () =>
    apiRequest<ApiUser>('/api/users/me'),

  changePassword: (currentPassword: string, newPassword: string) =>
    apiRequest<{ message: string }>('/api/users/me/password', {
      method: 'PUT',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  updateMySettings: (data: { book_delivery_email?: string }) =>
    apiRequest<ApiUser>('/api/users/me/settings', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  create: (user: { email: string; username: string; password: string; full_name?: string; is_admin?: boolean }) =>
    apiRequest<ApiUser>('/api/users/', {
      method: 'POST',
      body: JSON.stringify(user),
    }),

  getAll: (skip: number = 0, limit: number = 100) =>
    apiRequest<Array<ApiUser>>(`/api/users/?skip=${skip}&limit=${limit}`),

  getById: (id: number) =>
    apiRequest<ApiUser>(`/api/users/${id}`),

  update: (id: number, update: any) =>
    apiRequest<ApiUser>(`/api/users/${id}`, {
      method: 'PUT',
      body: JSON.stringify(update),
    }),

  delete: (id: number) =>
    apiRequest<void>(`/api/users/${id}`, {
      method: 'DELETE',
    }),

  resetPassword: (id: number, newPassword: string) =>
    apiRequest<{ message: string }>(`/api/users/${id}/password`, {
      method: 'PUT',
      body: JSON.stringify({ new_password: newPassword }),
    }),
};

// Settings API endpoints
export const settingsApi = {
  checkHardcoverToken: () =>
    apiRequest<{ has_hardcover_token: boolean }>('/api/settings/hardcover-token/check'),

  getHardcoverToken: () =>
    apiRequest<{ hardcover_api_token: string | null; hardcover_api_token_source: string; has_hardcover_token: boolean }>('/api/settings/hardcover-token'),

  setHardcoverToken: (token: string) =>
    apiRequest<{ message: string }>('/api/settings/hardcover-token', {
      method: 'PUT',
      body: JSON.stringify({ hardcover_api_token: token }),
    }),

  getDownloadPaths: () =>
    apiRequest<{ ebook_download_path: string | null; audiobook_download_path: string | null; use_hardlinks: boolean; use_hardlinks_ebook: boolean; use_hardlinks_audiobook: boolean }>('/api/settings/download-paths'),

  updateDownloadPaths: (paths: { ebook_download_path?: string; audiobook_download_path?: string; use_hardlinks?: boolean; use_hardlinks_ebook?: boolean; use_hardlinks_audiobook?: boolean }) =>
    apiRequest<{ message: string }>('/api/settings/download-paths', {
      method: 'PUT',
      body: JSON.stringify(paths),
    }),

  // Cache management (admin only)
  getCacheResources: () =>
    apiRequest<{ resources: Array<{ key: string; name: string; description: string }> }>('/api/settings/cache/resources'),

  clearCacheResource: (resource: string) =>
    apiRequest<{ message: string; deleted_count: number }>(`/api/settings/cache/clear/${resource}`, {
      method: 'POST',
    }),

  clearAllCache: () =>
    apiRequest<{ message: string; total_deleted: number; by_resource: Record<string, number> }>('/api/settings/cache/clear-all', {
      method: 'POST',
    }),

  debugCacheKeys: () =>
    apiRequest<{ total_keys: number; sample_keys: string[]; namespace: string }>('/api/settings/cache/debug'),

  browseDirectories: (path: string) =>
    apiRequest<{
      current_path: string;
      parent_path: string | null;
      directories: Array<{ name: string; path: string }>;
      error: string | null;
    }>(`/api/settings/browse-directories?path=${encodeURIComponent(path)}`),

  getOidcSettings: () =>
    apiRequest<OidcSettingsResponse>('/api/settings/oidc'),

  updateOidcSettings: (settings: Record<string, string>) =>
    apiRequest<{ message: string }>('/api/settings/oidc', {
      method: 'PUT',
      body: JSON.stringify(settings),
    }),

  testOidcConnection: () =>
    apiRequest<{ status: string; issuer: string; authorization_endpoint: string; token_endpoint: string; userinfo_endpoint: string }>('/api/settings/oidc/test', {
      method: 'POST',
    }),

  getSmtpSettings: () =>
    apiRequest<SmtpSettingsResponse>('/api/settings/smtp'),

  updateSmtpSettings: (data: {
    smtp_host?: string;
    smtp_port?: number;
    smtp_encryption?: string;
    smtp_username?: string;
    smtp_from_address?: string;
    smtp_password?: string;
  }) =>
    apiRequest<SmtpSettingsResponse>('/api/settings/smtp', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  testSmtpSettings: (recipient?: string) =>
    apiRequest<{ message: string }>('/api/settings/smtp/test', {
      method: 'POST',
      body: JSON.stringify({ recipient: recipient || null }),
    }),
};

export interface SmtpSettingsResponse {
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_encryption: string;
  smtp_username: string | null;
  smtp_from_address: string | null;
  smtp_password_set: boolean;
  configured: boolean;
}

export interface EmailLog {
  id: number;
  recipient: string;
  subject: string | null;
  book_title: string | null;
  book_format: string | null;
  status: string;
  error_message: string | null;
  created_at: string | null;
}

export const emailsApi = {
  getAll: () => apiRequest<EmailLog[]>('/api/emails/'),
};

// Readarr API endpoints
export const readarrApi = {
  getAll: () =>
    apiRequest<Array<any>>('/api/readarr/'),

  getById: (id: number) =>
    apiRequest<any>(`/api/readarr/${id}`),

  create: (server: any) =>
    apiRequest<any>('/api/readarr/', {
      method: 'POST',
      body: JSON.stringify(server),
    }),

  update: (id: number, server: any) =>
    apiRequest<any>(`/api/readarr/${id}`, {
      method: 'PUT',
      body: JSON.stringify(server),
    }),

  delete: (id: number) =>
    apiRequest<void>(`/api/readarr/${id}`, {
      method: 'DELETE',
    }),

  testConnection: (config: { hostname: string; port: number; use_ssl: boolean; api_key: string; url_base?: string }) =>
    apiRequest<{
      success: boolean;
      error?: string;
      quality_profiles?: Array<{ id: number; name: string }>;
      root_folders?: Array<{ path: string; freeSpace?: number; totalSpace?: number }>;
      tags?: Array<{ id: number; label: string }>;
    }>('/api/readarr/test-connection', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  getConfiguredFormats: () =>
    apiRequest<{ ebook: boolean; audiobook: boolean }>('/api/readarr/configured-formats'),

  getAvailability: (hardcoverId: number) =>
    apiRequest<{ ebook: boolean; audiobook: boolean }>(`/api/readarr/availability/${hardcoverId}`),

  getAvailabilityBatch: (hardcoverIds: number[], isbnMap?: Record<number, string[]>) =>
    apiRequest<{ results: Array<{ hardcover_id: number; ebook: boolean; audiobook: boolean }> }>(
      '/api/readarr/availability/batch',
      {
        method: 'POST',
        body: JSON.stringify({ hardcover_ids: hardcoverIds, isbn_map: isbnMap }),
      }
    ),

  getAvailabilityByReadarrId: (readarrBookId: number, format: 'ebook' | 'audiobook') =>
    apiRequest<{ available: boolean; readarr_book_id: number; format: string }>(
      `/api/readarr/availability/readarr/${readarrBookId}?format=${format}`
    ),

  getManageLink: (hardcoverId: number, format?: 'ebook' | 'audiobook') =>
    apiRequest<{ url: string | null; format: string | null; readarr_book_id: number | null }>(
      `/api/readarr/manage-link/${hardcoverId}${format ? `?format=${format}` : ''}`
    ),
};

// Jobs API endpoints
export interface Job {
  name: string;
  type: string;
  interval_seconds: number;
  last_execution: string | null;
  next_execution: string | null;
  is_enabled?: boolean;
}

export interface IntervalOption {
  value: number;
  label: string;
}

export interface JobRunStatus {
  run_id: string;
  job_name: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error: string | null;
}

export const jobsApi = {
  getAll: () =>
    apiRequest<Array<Job>>('/api/jobs/', {
      method: 'GET',
    }),

  getIntervals: () =>
    apiRequest<Array<IntervalOption>>('/api/jobs/intervals', {
      method: 'GET',
    }),

  update: (jobName: string, intervalSeconds: number) =>
    apiRequest<Job>(`/api/jobs/${jobName}`, {
      method: 'PUT',
      body: JSON.stringify({ interval_seconds: intervalSeconds }),
    }),

  run: (jobName: string) =>
    apiRequest<{
      message: string;
      job_name: string;
      run_id: string;
      triggered_at: string;
    }>(`/api/jobs/${jobName}/run`, {
      method: 'POST',
    }),

  getStatus: (runId: string) =>
    apiRequest<JobRunStatus>(`/api/jobs/status/${runId}`, {
      method: 'GET',
    }),
};

// Booklore API endpoints
export interface BookloreServer {
  id: number;
  name: string;
  url: string;
  username: string;
  is_default: boolean;
  ebook_library_id: number | null;
  audiobook_library_id: number | null;
  created_at: string;
  updated_at?: string;
}

export interface BookloreTestResponse {
  success: boolean;
  error?: string;
  libraries?: Array<{ id: number; name: string }>;
}

export const bookloreApi = {
  getAll: () =>
    apiRequest<Array<BookloreServer>>('/api/booklore/'),

  getById: (id: number) =>
    apiRequest<BookloreServer>(`/api/booklore/${id}`),

  create: (server: { name: string; url: string; username: string; password: string; is_default?: boolean; ebook_library_id?: number | null; audiobook_library_id?: number | null }) =>
    apiRequest<BookloreServer>('/api/booklore/', {
      method: 'POST',
      body: JSON.stringify(server),
    }),

  update: (id: number, server: { name?: string; url?: string; username?: string; password?: string; is_default?: boolean; ebook_library_id?: number | null; audiobook_library_id?: number | null }) =>
    apiRequest<BookloreServer>(`/api/booklore/${id}`, {
      method: 'PUT',
      body: JSON.stringify(server),
    }),

  delete: (id: number) =>
    apiRequest<void>(`/api/booklore/${id}`, {
      method: 'DELETE',
    }),

  testConnection: (config: { url: string; username: string; password: string }) =>
    apiRequest<BookloreTestResponse>('/api/booklore/test', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  getBooks: (serverId: number) =>
    apiRequest<Array<any>>(`/api/booklore/${serverId}/books`),

  checkBook: (serverId: number, hardcoverId: number) =>
    apiRequest<{ available: boolean; book?: any }>(`/api/booklore/${serverId}/check/${hardcoverId}`),
};

// Audiobookshelf API endpoints
export interface AudiobookshelfServer {
  id: number;
  name: string;
  url: string;
  is_default: boolean;
  library_id: string | null;
  created_at: string;
  updated_at?: string;
}

export interface AudiobookshelfTestResponse {
  success: boolean;
  error?: string;
  libraries?: Array<{ id: string; name: string; mediaType: string }>;
}

export interface AudiobookshelfLibraryItem {
  id: string;
  title: string;
  author: string | null;
  narrator: string | null;
  series: string | null;
  isbn: string | null;
  published_year: string | null;
  description: string | null;
  duration_seconds: number | null;
  num_tracks: number | null;
  has_cover: boolean;
  added_at: number | null;
  // Present when the item is already matched to a catalog book.
  hardcover_id: number | null;
  book_id: number | null;
  ebook_available: boolean;
  audiobook_available: boolean;
}

export interface AudiobookshelfResolveResponse {
  hardcover_id: number;
  book_id: number;
  ebook_available: boolean;
  audiobook_available: boolean;
}

export const audiobookshelfApi = {
  getAll: () =>
    apiRequest<Array<AudiobookshelfServer>>('/api/audiobookshelf/'),

  getById: (id: number) =>
    apiRequest<AudiobookshelfServer>(`/api/audiobookshelf/${id}`),

  create: (server: { name: string; url: string; api_key: string; is_default?: boolean; library_id?: string | null }) =>
    apiRequest<AudiobookshelfServer>('/api/audiobookshelf/', {
      method: 'POST',
      body: JSON.stringify(server),
    }),

  update: (id: number, server: { name?: string; url?: string; api_key?: string; is_default?: boolean; library_id?: string | null }) =>
    apiRequest<AudiobookshelfServer>(`/api/audiobookshelf/${id}`, {
      method: 'PUT',
      body: JSON.stringify(server),
    }),

  delete: (id: number) =>
    apiRequest<void>(`/api/audiobookshelf/${id}`, {
      method: 'DELETE',
    }),

  testConnection: (config: { url: string; api_key: string }) =>
    apiRequest<AudiobookshelfTestResponse>('/api/audiobookshelf/test', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  getItems: (serverId: number) =>
    apiRequest<Array<any>>(`/api/audiobookshelf/${serverId}/items`),

  // Every item in the default Audiobookshelf library (any signed-in user).
  getLibraryItems: () =>
    apiRequest<Array<AudiobookshelfLibraryItem>>('/api/audiobookshelf/library/items'),

  coverDataUrl: (itemId: string) =>
    authedDataUrl(`/api/audiobookshelf/library/items/${encodeURIComponent(itemId)}/cover`),

  // Match an ABS item to a catalog book (creating one via Hardcover if needed)
  // so the UI can open its details page.
  resolveItem: (itemId: string) =>
    apiRequest<AudiobookshelfResolveResponse>(
      `/api/audiobookshelf/library/items/${encodeURIComponent(itemId)}/resolve`,
    ),
};

// Download Settings API endpoints
export interface ProwlarrServer {
  id: number;
  name: string;
  host: string;
  port: number;
  use_ssl: boolean;
  api_key: string;
  url_base: string | null;
  enabled: boolean;
  is_default: boolean;
  indexer_ids: number[] | null;
}

export interface ProwlarrIndexer {
  id: number;
  name: string;
  protocol: string;
  privacy: string;
  enabled: boolean;
}

export interface DownloadClient {
  id: number;
  name: string;
  type: string;
  protocol: string;
  host: string;
  port: number;
  use_ssl: boolean;
  username: string | null;
  password: string;
  api_key: string | null;
  url_base: string | null;
  enabled: boolean;
  priority: number;
  category: string | null;
  ebook_category: string | null;
  audiobook_category: string | null;
  ebook_download_path: string | null;
  audiobook_download_path: string | null;
  path_mappings_json: string | null;
}

export interface ProwlarrTestResponse {
  success: boolean;
  error?: string;
  indexers?: Array<any>;
  total_indexers?: number;
}

export interface DownloadClientTestResponse {
  success: boolean;
  error?: string;
  version?: string;
  api_version?: string;
}

export const downloadSettingsApi = {
  // Prowlarr endpoints
  getProwlarrServers: () =>
    apiRequest<Array<ProwlarrServer>>('/api/download-settings/prowlarr'),

  createProwlarrServer: (server: { name: string; host: string; port?: number; use_ssl?: boolean; api_key: string; url_base?: string; enabled?: boolean; is_default?: boolean; indexer_ids?: number[] }) =>
    apiRequest<ProwlarrServer>('/api/download-settings/prowlarr', {
      method: 'POST',
      body: JSON.stringify(server),
    }),

  updateProwlarrServer: (id: number, server: { name?: string; host?: string; port?: number; use_ssl?: boolean; api_key?: string; url_base?: string; enabled?: boolean; is_default?: boolean; indexer_ids?: number[] }) =>
    apiRequest<ProwlarrServer>(`/api/download-settings/prowlarr/${id}`, {
      method: 'PUT',
      body: JSON.stringify(server),
    }),

  deleteProwlarrServer: (id: number) =>
    apiRequest<{ message: string }>(`/api/download-settings/prowlarr/${id}`, {
      method: 'DELETE',
    }),

  testProwlarrConnection: (config: { host: string; port: number; use_ssl: boolean; api_key: string; url_base?: string }) =>
    apiRequest<ProwlarrTestResponse>('/api/download-settings/prowlarr/test', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  getProwlarrIndexers: (serverId: number) =>
    apiRequest<{ indexers: ProwlarrIndexer[] }>(`/api/download-settings/prowlarr/${serverId}/indexers`),

  // Download Client endpoints
  getDownloadClients: () =>
    apiRequest<Array<DownloadClient>>('/api/download-settings/download-clients'),

  createDownloadClient: (client: { name: string; type: string; protocol: string; host: string; port: number; use_ssl?: boolean; username?: string; password?: string; api_key?: string; url_base?: string; enabled?: boolean; priority?: number; category?: string; ebook_category?: string; audiobook_category?: string; ebook_download_path?: string; audiobook_download_path?: string; path_mappings_json?: string }) =>
    apiRequest<DownloadClient>('/api/download-settings/download-clients', {
      method: 'POST',
      body: JSON.stringify(client),
    }),

  updateDownloadClient: (id: number, client: { name?: string; type?: string; protocol?: string; host?: string; port?: number; use_ssl?: boolean; username?: string; password?: string; api_key?: string; url_base?: string; enabled?: boolean; priority?: number; category?: string; ebook_category?: string; audiobook_category?: string; ebook_download_path?: string; audiobook_download_path?: string; path_mappings_json?: string }) =>
    apiRequest<DownloadClient>(`/api/download-settings/download-clients/${id}`, {
      method: 'PUT',
      body: JSON.stringify(client),
    }),

  deleteDownloadClient: (id: number) =>
    apiRequest<{ message: string }>(`/api/download-settings/download-clients/${id}`, {
      method: 'DELETE',
    }),

  testDownloadClient: (config: { type: string; protocol: string; host: string; port: number; use_ssl: boolean; username?: string; password?: string; api_key?: string; url_base?: string }) =>
    apiRequest<DownloadClientTestResponse>('/api/download-settings/download-clients/test', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
};

// Downloads API endpoints
export interface ReleaseInfo {
  title: string;
  download_url: string;
  protocol: string;
  indexer: string;
  size_bytes: number;
  seeders: number | null;
  format: string | null;
  language: string | null;
  quality_score: number;
  published_date: string | null;
  already_downloaded: boolean;
  info_url: string | null;
}

export interface SearchResponse {
  book_id: number;
  releases: ReleaseInfo[];
  total: number;
}

export interface DownloadResponse {
  task_id: number;
  status: string;
  message: string;
}

export interface DownloadTask {
  id: number;
  book_id: number;
  format: string;
  source: string;
  release_title: string;
  download_url: string;
  protocol: string;
  state: string;
  progress: number;
  download_path: string | null;
  import_status?: string;
  import_message?: string;
  imported_at?: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  message?: string;
}

export interface DownloadAttempt {
  source_name: string;
  source_type: string;
  url: string;
  started_at: string;
  ended_at: string | null;
  success: boolean;
  error: string | null;
  bytes_downloaded: number;
}

export interface DownloadLog {
  task_id: number;
  started_at: string | null;
  completed_at: string | null;
  final_result: string | null;
  attempts: DownloadAttempt[];
  message?: string;
}

export const downloadsApi = {
  // Search for releases
  // source: 'prowlarr' | 'direct' | undefined (undefined = all sources)
  searchReleases: (bookId: number, formatType: 'ebook' | 'audiobook', source?: 'prowlarr' | 'direct') => {
    let url = `/api/downloads/search/${bookId}?format_type=${formatType}`;
    if (source) {
      url += `&source_filter=${source}`;
    }
    return apiRequest<SearchResponse>(url, { method: 'POST' });
  },

  // Manually download a specific release
  downloadRelease: (request: {
    book_id: number;
    format_type: string;
    download_url: string;
    protocol: string;
    release_title: string;
    indexer?: string;
    size_bytes?: number;
  }) =>
    apiRequest<DownloadResponse>('/api/downloads/download', {
      method: 'POST',
      body: JSON.stringify(request),
    }),

  // Automatically download the best release
  autoDownload: (bookId: number, formatType: 'ebook' | 'audiobook') =>
    apiRequest<DownloadResponse>(`/api/downloads/auto-download/${bookId}?format_type=${formatType}`, {
      method: 'POST',
    }),

  // Get download tasks
  getTasks: (skip?: number, limit?: number, state?: string) => {
    const params = new URLSearchParams();
    if (skip !== undefined) params.set('skip', String(skip));
    if (limit !== undefined) params.set('limit', String(limit));
    if (state) params.set('state', state);
    const query = params.toString();
    return apiRequest<DownloadTask[]>(`/api/downloads/tasks${query ? `?${query}` : ''}`);
  },

  // Get download log for a specific task (direct downloads only)
  getTaskLog: (taskId: number) =>
    apiRequest<DownloadLog>(`/api/downloads/tasks/${taskId}/log`),

  // Manually import a download to the configured destination
  importDownload: (taskId: number) =>
    apiRequest<{ success: boolean; message: string; destination_path: string }>(
      `/api/downloads/import/${taskId}`,
      { method: 'POST' }
    ),

  // Delete a single download task
  deleteTask: (taskId: number) =>
    apiRequest<{ success: boolean; message: string }>(
      `/api/downloads/task/${taskId}`,
      { method: 'DELETE' }
    ),

  // Clear all eligible download tasks
  clearTasks: () =>
    apiRequest<{ success: boolean; message: string; deleted_count: number }>(
      '/api/downloads/tasks/clear',
      { method: 'DELETE' }
    ),
};

// Direct Download Settings types
export interface DirectDownloadSettings {
  id: number;
  enabled: boolean;
  annas_archive_enabled: boolean;
  annas_archive_mirror: string | null;
  zlibrary_enabled: boolean;
  zlibrary_email: string | null;
  zlibrary_password_set: boolean;
  zlibrary_domain: string | null;
  requests_per_minute: number;
  flaresolverr_url: string | null;
}

export interface DirectDownloadTestResponse {
  success: boolean;
  providers_count: number;
  providers_status: Record<string, boolean>;
  message?: string;
}

// Direct Download API
export const directDownloadApi = {
  getSettings: () =>
    apiRequest<DirectDownloadSettings>('/api/direct-downloads/settings'),

  updateSettings: (data: {
    enabled: boolean;
    annas_archive_enabled: boolean;
    annas_archive_mirror?: string;
    zlibrary_enabled: boolean;
    zlibrary_email?: string;
    zlibrary_password?: string;
    zlibrary_domain?: string;
    requests_per_minute: number;
    flaresolverr_url?: string | null;
  }) =>
    apiRequest<DirectDownloadSettings>('/api/direct-downloads/settings', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  testConnection: () =>
    apiRequest<DirectDownloadTestResponse>('/api/direct-downloads/test', {
      method: 'POST',
    }),

  testFlaresolverr: (url: string) =>
    apiRequest<{ success: boolean; message: string }>('/api/direct-downloads/test-flaresolverr', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  resetSettings: () =>
    apiRequest<{ success: boolean; message: string }>('/api/direct-downloads/settings', {
      method: 'DELETE',
    }),
};

// Hardcover Sync types
export interface HardcoverSyncConfig {
  is_enabled: boolean;
  sync_to_read: boolean;
  sync_list_ids: number[];
  default_format: string;
  last_synced_at: string | null;
  has_token: boolean;           // true if any token available (personal or global)
  has_personal_token: boolean;  // true only if user set their own token
  using_app_token: boolean;     // true if falling back to global app token
}

export interface HardcoverList {
  id: number;
  name: string;
}

// Hardcover Sync API
export const hardcoverSyncApi = {
  getConfig: () =>
    apiRequest<HardcoverSyncConfig>('/api/hardcover-sync/config'),

  updateConfig: (data: {
    hardcover_api_token?: string;
    clear_token?: boolean;
    is_enabled?: boolean;
    sync_to_read?: boolean;
    sync_list_ids?: number[];
    default_format?: string;
  }) =>
    apiRequest<HardcoverSyncConfig>('/api/hardcover-sync/config', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  getLists: () =>
    apiRequest<HardcoverList[]>('/api/hardcover-sync/lists'),

  runSync: () =>
    apiRequest<{ message: string }>('/api/hardcover-sync/run', {
      method: 'POST',
    }),
};

// ---------------------------------------------------------------------------
// Calibre library ("My Books")
// ---------------------------------------------------------------------------
export interface CalibreSettings {
  id: number;
  library_path: string | null;
  enabled: boolean;
  valid: boolean;
  book_count: number | null;
  error: string | null;
}

/** Fields added by the local metadata overlay (calibre_book_links). */
export interface CalibreOverlayFields {
  metadata_source?: 'calibre' | 'overlay';
  metadata_locked?: boolean;
  linked_book_id?: number | null;
  link_source?: 'download' | 'manual' | 'fuzzy' | null;
  link_confirmed?: boolean;
  hardcover_id?: number | null;
  overlay_cover_url?: string | null;
  page_count?: number | null;
  genres?: string[] | null;
}

export interface CalibreBook extends CalibreOverlayFields {
  id: number;
  title: string;
  authors: string;
  series: string | null;
  series_index: number | null;
  rating: number | null;
  pubdate: string | null;
  added: string | null;
  has_cover: boolean;
  formats: string[];
}

export interface CalibreBookDetail extends CalibreBook {
  description: string | null;
  tags: string[];
  publisher: string | null;
  languages: string[];
  identifiers: Record<string, string>;
  format_details: Array<{ format: string; size: number | null; name: string }>;
}

export interface CalibreOverlaySettings {
  enabled: boolean;
  prefer_local: boolean;
}

export interface CalibreLinkResponse {
  linked_book_id: number | null;
  link_source: string | null;
  link_confirmed: boolean;
  hardcover_id: number | null;
}

export interface CalibreByHardcover {
  calibre_book_id: number;
  title: string | null;
  link_source: string | null;
  link_confirmed: boolean;
  ebook_formats: string[];
  audiobook_formats: string[];
  format_details: Array<{ format: string; size: number | null; name: string }>;
}

export interface CalibreBooksResponse {
  books: CalibreBook[];
  total: number;
  page: number;
  page_size: number;
}

export type MetadataSource =
  | 'current'
  | 'googlebooks'
  | 'applebooks'
  | 'openlibrary'
  | 'hardcover';

export interface MetadataCandidate {
  source: MetadataSource;
  found: boolean;
  note: string | null;
  title: string | null;
  author: string | null;
  publisher: string | null;
  isbn: string | null;
  description: string | null;
  cover_url: string | null;
  page_count: number | null;
  published_date: string | null;
  rating: number | null;
  ratings_count: number | null;
  genres: string[];
  series: string | null;
  series_position: number | null;
}

export interface MetadataCandidatesResponse {
  linked_book_id: number | null;
  current: MetadataCandidate;
  candidates: MetadataCandidate[];
}

export interface ApplyMetadataResponse {
  linked_book_id: number;
  hardcover_id: number | null;
  current: MetadataCandidate;
}

export type CalibreSort = 'title' | 'author' | 'added' | 'pubdate';

/** Fetch a binary endpoint with the bearer token and return it as a Blob. */
async function authedBlob(endpoint: string): Promise<Blob> {
  const doFetch = (token: string | null) =>
    fetch(`${API_BASE_URL}${endpoint}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });

  let response = await doFetch(getAccessToken());
  if (response.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      response = await doFetch(getAccessToken());
    }
  }
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.blob();
}

/** Fetch a binary endpoint and return it as a data: URL.
 *  Unlike an object URL this needs no revoke and is safe to cache/share across
 *  components and route changes. */
async function authedDataUrl(endpoint: string): Promise<string> {
  const blob = await authedBlob(endpoint);
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

export const calibreApi = {
  getSettings: () => apiRequest<CalibreSettings>('/api/calibre/settings'),

  updateSettings: (data: { library_path: string | null; enabled: boolean }) =>
    apiRequest<CalibreSettings>('/api/calibre/settings', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  test: (library_path: string) =>
    apiRequest<{ success: boolean; book_count: number | null; error: string | null }>(
      '/api/calibre/test',
      { method: 'POST', body: JSON.stringify({ library_path }) },
    ),

  getBooks: (params: { search?: string; sort?: CalibreSort; page?: number; pageSize?: number }) => {
    const q = new URLSearchParams();
    if (params.search) q.set('search', params.search);
    q.set('sort', params.sort ?? 'added');
    q.set('page', String(params.page ?? 1));
    q.set('page_size', String(params.pageSize ?? 50));
    return apiRequest<CalibreBooksResponse>(`/api/calibre/books?${q.toString()}`);
  },

  getBook: (id: number) => apiRequest<CalibreBookDetail>(`/api/calibre/books/${id}`),

  fetchCover: (id: number) => authedBlob(`/api/calibre/books/${id}/cover`),

  coverDataUrl: (id: number) => authedDataUrl(`/api/calibre/books/${id}/cover`),

  downloadFormat: async (id: number, format: string, filename?: string) => {
    const blob = await authedBlob(
      `/api/calibre/books/${id}/download?format=${encodeURIComponent(format)}`,
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || `book-${id}.${format.toLowerCase()}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

  emailBook: (id: number, format: string) =>
    apiRequest<{ success: boolean; message: string; recipient: string }>(
      `/api/calibre/books/${id}/email`,
      { method: 'POST', body: JSON.stringify({ format }) },
    ),

  getOverlaySettings: () =>
    apiRequest<CalibreOverlaySettings>('/api/calibre/overlay-settings'),

  updateOverlaySettings: (data: CalibreOverlaySettings) =>
    apiRequest<CalibreOverlaySettings>('/api/calibre/overlay-settings', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  linkBook: (id: number, target: { book_id?: number; hardcover_id?: number }) =>
    apiRequest<CalibreLinkResponse>(`/api/calibre/books/${id}/link`, {
      method: 'PUT',
      body: JSON.stringify(target),
    }),

  clearLink: (id: number) =>
    apiRequest<{ removed: boolean }>(`/api/calibre/books/${id}/link`, {
      method: 'DELETE',
    }),

  refreshMetadata: (id: number) =>
    apiRequest<CalibreLinkResponse>(`/api/calibre/books/${id}/refresh-metadata`, {
      method: 'POST',
    }),

  metadataCandidates: (id: number, opts?: { title?: string; author?: string }) => {
    const q = new URLSearchParams();
    if (opts?.title) q.set('title', opts.title);
    if (opts?.author) q.set('author', opts.author);
    const qs = q.toString();
    return apiRequest<MetadataCandidatesResponse>(
      `/api/calibre/books/${id}/metadata-candidates${qs ? `?${qs}` : ''}`,
    );
  },

  applyMetadata: (
    id: number,
    body: {
      source: Exclude<MetadataSource, 'current'>;
      fields?: string[];
      title?: string;
      author?: string;
    },
  ) =>
    apiRequest<ApplyMetadataResponse>(`/api/calibre/books/${id}/apply-metadata`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Resolve a Hardcover id to its linked Calibre library book, or null. */
  getByHardcover: async (hardcoverId: number): Promise<CalibreByHardcover | null> => {
    try {
      return await apiRequest<CalibreByHardcover>(
        `/api/calibre/by-hardcover/${hardcoverId}`,
      );
    } catch {
      return null;
    }
  },
};
