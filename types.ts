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

/** 更新履歴の 1 件。日付は JAF の掲載日で、こちらが検出した日ではない。 */
export interface HistoryEvent {
  date: string;
  /** listed: JAF に掲載された / updated: JAF が差し替えた
   *  edition: 同じ規則の別年度版が公開された / removed: 一覧から消えた */
  type: 'listed' | 'updated' | 'edition' | 'removed';
  /** type が edition のときだけ。切り替わり先の版 */
  docId?: string;
  edition?: string;
}

/** 同じ規則の年度版 1 件 */
export interface EditionRef {
  docId: string;
  title: string;
  edition: string | null;
  uploadDate: string | null;
  current: boolean;
}

/** 差分の集計。「軽微」という区分は置かない。
 *  類似度で軽重を決めると、走行距離が 30km→20分 に変わった条が
 *  類似度 0.971 で「軽微」に分類されてしまうため（実測）。
 *  yearOnly は「年号を伏せたら同一」という厳密な条件で判定している。 */
export interface DiffSummary {
  clauses: number;
  unchanged: number;
  changed: number;
  yearOnly: number;
  added: number;
  removed: number;
}

/** 差分の一覧の 1 件（規則ページから差分ページへ案内するのに使う） */
export interface DiffRef {
  baseDocId: string;
  baseTitle: string | null;
  baseUploadDate: string | null;
  /** revision: 同じ規則の前の版 / edition: 前年度版 */
  kind: 'revision' | 'edition';
  summary: DiffSummary;
}

export type DiffStatus = 'changed' | 'year_only' | 'added' | 'removed';

export interface DiffLine {
  op: 'add' | 'del';
  text: string;
}

export interface DiffChange {
  status: DiffStatus;
  /** 条項番号の階層（第1章 > 第4条） */
  key: string;
  heading: string;
  anchor: string | null;
  page?: number | null;
  previousPage?: number | null;
  similarity?: number;
  lines: DiffLine[];
}

/** /api/documents/{docId}/diff/{baseDocId} */
export interface DocumentDiff {
  kind: 'revision' | 'edition';
  generatedAt: string;
  base: { docId: string | null; title: string | null; uploadDate: string | null };
  target: { docId: string | null; title: string | null; uploadDate: string | null };
  summary: DiffSummary;
  changes: DiffChange[];
}

/** JAF の公示に添付された PDF。対比表なら JAF 自身の新旧対照。 */
export interface NoticeAttachment {
  text: string;
  url: string;
  comparison: boolean;
}

/** JAF の公示 1 件（規則に紐づけたもの） */
export interface Notice {
  id: string;
  date: string | null;
  noticeNo: string | null;
  title: string;
  url: string;
  attachments: NoticeAttachment[];
  hasComparison: boolean;
}

/** /api/documents/{docId}/history */
export interface DocumentHistory {
  docId: string;
  series: string | null;
  edition: string | null;
  events: HistoryEvent[];
  /** 年度版が 1 つしかなければ空配列 */
  editions: EditionRef[];
  /** 条単位の改正差分が取れる相手 */
  diffs: DiffRef[];
  /** この規則に関する JAF の公示（新しい順） */
  announcements: Notice[];
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

/** /api/ask が最初に返す根拠チャンク */
export interface AskSource {
  index: number;
  docId: string;
  title: string;
  heading: string;
  headingPath: string;
  page: number;
  anchor: string | null;
  /** アプリ内の該当箇所への直リンク */
  url: string;
  pdfUrl: string | null;
  excerpt: string;
}

export interface AskStatus {
  available: boolean;
  hasApiKey: boolean;
  /** ベクトル検索が有効なチャンク数（0 なら全文検索のみ） */
  vectors: number;
  hybrid: boolean;
  chatModel: string;
  embedModel: string;
}
