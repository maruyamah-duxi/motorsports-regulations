import type { DocumentsResponse, RegulationDocument, SearchResponse } from '../types';

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
