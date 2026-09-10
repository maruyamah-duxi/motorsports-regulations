/** History API を使う最小ルータ。
 *
 *  ハッシュルータにすると規則本文内のアンカー（#第12条-安全ベルト）と
 *  衝突するため、実パスで持つ。サーバ側は未知のパスに index.html を返す
 *  （server/app.py の catch-all）。 */

import { useCallback, useEffect, useState } from 'react';

type Listener = () => void;
const listeners = new Set<Listener>();

function currentLocation(): string {
  return window.location.pathname + window.location.search;
}

function notify(): void {
  listeners.forEach((fn) => fn());
}

export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (to === currentLocation()) return;
  if (options.replace) {
    window.history.replaceState(null, '', to);
  } else {
    window.history.pushState(null, '', to);
  }
  notify();
}

export function useLocation(): string {
  const [loc, setLoc] = useState(currentLocation);
  useEffect(() => {
    const update = () => setLoc(currentLocation());
    listeners.add(update);
    window.addEventListener('popstate', update);
    return () => {
      listeners.delete(update);
      window.removeEventListener('popstate', update);
    };
  }, []);
  return loc;
}

export interface Route {
  name: 'home' | 'search' | 'document' | 'ask' | 'diff';
  docId?: string;
  /** diff のときの比較相手。'previous' なら同じ規則の前の版 */
  baseDocId?: string;
  query?: string;
  anchor?: string;
}

export function parseRoute(location: string): Route {
  const [pathname, search] = location.split('?');
  const params = new URLSearchParams(search || '');
  const segments = pathname.split('/').filter(Boolean);

  if (segments[0] === 'search') {
    return { name: 'search', query: params.get('q') || '' };
  }
  if (segments[0] === 'ask') {
    return { name: 'ask', query: params.get('q') || '' };
  }
  if (segments[0] === 'doc' && segments[1]) {
    return { name: 'document', docId: decodeURIComponent(segments[1]) };
  }
  if (segments[0] === 'diff' && segments[1] && segments[2]) {
    return {
      name: 'diff',
      docId: decodeURIComponent(segments[1]),
      baseDocId: decodeURIComponent(segments[2]),
    };
  }
  return { name: 'home', query: params.get('q') || '' };
}

/** 内部リンク。修飾キー付きクリックや別タブは既定動作に任せる。 */
export function useLinkHandler(): (event: React.MouseEvent<HTMLAnchorElement>) => void {
  return useCallback((event: React.MouseEvent<HTMLAnchorElement>) => {
    const anchor = event.currentTarget;
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      anchor.target === '_blank'
    ) {
      return;
    }
    const href = anchor.getAttribute('href') || '';
    if (!href.startsWith('/') || href.startsWith('//')) return;
    event.preventDefault();
    navigate(href);
    window.scrollTo({ top: 0 });
  }, []);
}
