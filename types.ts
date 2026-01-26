export interface Regulation {
  id: string;
  category: string;
  subCategory?: string; // Derived from the accordion headers in the HTML
  title: string;
  url: string;
  updateDate: string;
  summary: string;
  content_markdown: string; // Detailed content structure for search and display
  content_tags: string[];
}

export enum ViewMode {
  LIST = 'LIST',
  ANALYTICS = 'ANALYTICS',
}