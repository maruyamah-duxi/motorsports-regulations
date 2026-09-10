import type {
  AskSource,
  AskStatus,
  DocumentHistory,
  DocumentsResponse,
  RegulationDocument,
  SearchResponse,
} from '../types';

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* JSON でないレスポンスはそのまま */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function fetchDocuments(signal?: AbortSignal): Promise<DocumentsResponse> {
  return getJson<DocumentsResponse>('/api/documents', signal);
}

export function fetchDocument(docId: string, signal?: AbortSignal): Promise<RegulationDocument> {
  return getJson<RegulationDocument>(`/api/documents/${encodeURIComponent(docId)}`, signal);
}

export function fetchDocumentHistory(
  docId: string,
  signal?: AbortSignal,
): Promise<DocumentHistory> {
  return getJson<DocumentHistory>(
    `/api/documents/${encodeURIComponent(docId)}/history`,
    signal,
  );
}

export function search(
  query: string,
  opts: { limit?: number; offset?: number; doc?: string } = {},
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (opts.limit) params.set('limit', String(opts.limit));
  if (opts.offset) params.set('offset', String(opts.offset));
  if (opts.doc) params.set('doc', opts.doc);
  return getJson<SearchResponse>(`/api/search?${params}`, signal);
}

/** 図版など、規則ごとのアセットの URL */
export function assetUrl(docId: string, asset: string): string {
  return `/content/${encodeURIComponent(docId)}/${asset}`;
}

export function fetchAskStatus(signal?: AbortSignal): Promise<AskStatus> {
  return getJson<AskStatus>('/api/ask/status', signal);
}

export interface AskHandlers {
  onSources?: (sources: AskSource[]) => void;
  onDelta?: (text: string) => void;
  onError?: (message: string) => void;
  onDone?: (info: { cached?: boolean; sources?: number }) => void;
}

/** /api/ask の Server-Sent Events を読む。
 *  POST なので EventSource は使えず、fetch のストリームを自前で解く。 */
export async function askStream(
  question: string,
  history: { role: 'user' | 'assistant'; text: string }[],
  handlers: AskHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, history }),
    signal,
  });

  if (!res.ok || !res.body) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) message = body.detail;
    } catch {
      /* JSON でないレスポンスはそのまま */
    }
    handlers.onError?.(message);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = (block: string) => {
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    let payload: unknown;
    try {
      payload = JSON.parse(dataLines.join('\n'));
    } catch {
      return;
    }
    if (event === 'sources') handlers.onSources?.(payload as AskSource[]);
    else if (event === 'delta') handlers.onDelta?.(String(payload));
    else if (event === 'error') handlers.onError?.((payload as { message: string }).message);
    else if (event === 'done') handlers.onDone?.(payload as { cached?: boolean });
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      dispatch(buffer.slice(0, sep));
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}
