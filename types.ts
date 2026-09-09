/** パイプラインが生成する構造化データの型。
 *  pipeline/jafreg/convert.py の出力と 1 対 1 で対応する。 */

export type BlockType = 'heading' | 'paragraph' | 'figure' | 'table' | 'caption';

export interface Block {
  type: BlockType;
  page: number;
  bbox: [number, number, number, number];
  text?: string;
  level?: number;
  /** 見出しのアンカー ID（検索結果や AI 回答からの直リンク先） */
  id?: string;
  /** figure: content/<docId>/ からの相対パス */
  asset?: string;
  caption?: string;
  /** table: 行×列のセル */
  rows?: (string | null)[][];
  /** 条項番号（「第12条」「5.4.3）」など） */
  clause?: string;
}

export interface TocItem {
  id: string;
  level: number;
  text: string;
  page: number;
}

export interface DocumentStats {
  chars: number;
  figures: number;
  tables: number;
  ocrPages: number[];
}

export interface RegulationDocument {
  docId: string;
  title: string;
  source: string;
  sourceUrl: string | null;
  section: string;
  group: string;
  pdfUrl: string | null;
  uploadDate: string | null;
  pageCount: number;
  pipelineVersion: string;
  convertedAt: string;
  stats: DocumentStats;
  warnings: string[];
  toc: TocItem[];
  blocks: Block[];
}

/** /api/documents の 1 件（本文は含まない軽量版） */
export interface DocumentSummary {
  docId: string;
  title: string;
  source: string;
  section: string;
  group: string;
  uploadDate: string | null;
  pdfUrl: string | null;
  pageCount: number;
  chars: number;
  figures: number;
  tables: number;
}

export interface DocumentsResponse {
  builtAt: string | null;
  count: number;
  items: DocumentSummary[];
}

/** /api/search のヒット 1 件 */
export interface SearchHit {
  docId: string;
  title: string;
  section: string;
  group: string;
  heading: string;
  headingPath: string;
  clause: string | null;
  page: number;
  anchor: string | null;
  /** <mark> でハイライト済みの抜粋 */
  snippet: string;
  uploadDate: string | null;
  pdfUrl: string | null;
  url: string;
}

export interface SearchResponse {
  query: string;
  total: number;
  limit: number;
  offset: number;
  items: SearchHit[];
}
