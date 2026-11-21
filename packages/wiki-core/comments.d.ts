export declare function paragraphHash(text: string): string;
export declare function snippetOf(text: string): string;

export interface WikiComment {
  id: string;
  content: string;
  author: string;
  target: { kind: string; page: string; para_hash: string; snippet: string; selected_text?: string };
}
export interface CommentStore {
  list(page?: string): Promise<WikiComment[]>;
  add(input: { page: string; paraText: string; selectedText?: string; content: string }): Promise<unknown>;
}
export declare function createReviewstageCommentStore(opts: { endpoint?: string; materialId: string }): CommentStore;

export interface DemoCommentStore {
  list(): Promise<unknown[]>;
  add(input: { target: unknown; content: string }): Promise<unknown>;
}
export declare function createDemoCommentStore(opts: { endpoint?: string; materialId: string }): DemoCommentStore;
