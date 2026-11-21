import type { ILink, ILinkProvider, Terminal } from '@xterm/xterm';

// Keep these patterns in sync with server/utils/url-detection.js — the server
// reuses the same rules when it surfaces clickable links from raw PTY output.
// `|` and the Unicode box-drawing block are table borders, never URL content.
const URL_MATCH_REGEX = /https?:\/\/[^\s<>"'`\\\x1b\x07|\u2500-\u257F]+/gi;
const TRAILING_URL_CHAR_REGEX = /[)\]}>.,;:!?|\u2500-\u257F]+$/;
const TABLE_FRAGMENT_ROW_REGEX = /^[\s|\u2500-\u257F]*([A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)[\s|\u2500-\u257F]*$/;
const MIN_URL_LENGTH = 'https://a.b'.length;

/** A run of URL characters living on a single buffer row. `startX` is 1-based. */
type UrlPiece = {
  row: number;
  startX: number;
  text: string;
};

type LineSegment = {
  row: number;
  text: string;
  offset: number;
};

/**
 * Link provider that sees whole URLs instead of per-row fragments.
 *
 * The stock WebLinksAddon matches per visual row, so a URL hard-wrapped by the
 * terminal width (or clipped into a narrow table cell) is only clickable as
 * its first-row fragment and opens a truncated address. Here we rebuild the
 * logical line via xterm's wrapped-line flags and additionally extend a URL
 * that runs to the line end across following table rows that contain nothing
 * but a URL fragment between borders.
 */
class WrappedUrlLinkProvider implements ILinkProvider {
  constructor(private readonly terminal: Terminal) {}

  provideLinks(y: number, callback: (links: ILink[] | undefined) => void): void {
    const buffer = this.terminal.buffer.active;
    const row = y - 1;
    if (!buffer.getLine(row)) {
      callback(undefined);
      return;
    }

    // Rebuild the logical line: `isWrapped` marks a row as a continuation of
    // the previous one.
    let startRow = row;
    while (startRow > 0 && buffer.getLine(startRow)?.isWrapped) startRow--;
    let endRow = row;
    while (endRow + 1 < buffer.length && buffer.getLine(endRow + 1)?.isWrapped) endRow++;

    const segments: LineSegment[] = [];
    let combined = '';
    for (let index = startRow; index <= endRow; index++) {
      const text = buffer.getLine(index)?.translateToString(true) ?? '';
      segments.push({ row: index, text, offset: combined.length });
      combined += text;
    }
    if (!combined) {
      callback(undefined);
      return;
    }

    const links: ILink[] = [];
    for (const match of combined.matchAll(URL_MATCH_REGEX)) {
      const startOffset = match.index ?? 0;
      const endOffset = startOffset + match[0].length;

      // Split the match into per-row pieces so wrapped rows highlight fully.
      const pieces: UrlPiece[] = [];
      for (const segment of segments) {
        const pieceStart = Math.max(startOffset, segment.offset);
        const pieceEnd = Math.min(endOffset, segment.offset + segment.text.length);
        if (pieceStart < pieceEnd) {
          pieces.push({
            row: segment.row,
            startX: pieceStart - segment.offset + 1,
            text: combined.slice(pieceStart, pieceEnd),
          });
        }
      }
      if (pieces.length === 0) continue;
      // Each row reports only the links that start on it; xterm queries every
      // viewport row, so continuation rows must not re-emit the same link.
      if (pieces[0].row !== row) continue;

      // A URL running to the end of the logical line may continue inside a
      // table cell on the following rows (borders/padding around the fragment).
      if (endOffset === combined.length) {
        let nextRow = endRow + 1;
        for (;;) {
          const rowText = buffer.getLine(nextRow)?.translateToString(true);
          if (rowText === undefined) break;
          const fragment = rowText.match(TABLE_FRAGMENT_ROW_REGEX);
          if (!fragment) break;
          pieces.push({
            row: nextRow,
            startX: (fragment.index ?? 0) + 1,
            text: fragment[1],
          });
          nextRow++;
        }
      }

      // Drop trailing punctuation / table-border debris from the tail pieces.
      for (;;) {
        const last = pieces[pieces.length - 1];
        const trimmed = last.text.replace(TRAILING_URL_CHAR_REGEX, '');
        if (trimmed === last.text) break;
        if (trimmed) {
          last.text = trimmed;
          break;
        }
        pieces.pop();
        if (pieces.length === 0) break;
      }
      const url = pieces.map((piece) => piece.text).join('');
      if (pieces.length === 0 || url.length < MIN_URL_LENGTH) continue;

      const first = pieces[0];
      const last = pieces[pieces.length - 1];
      links.push({
        range: {
          start: { x: first.startX, y: first.row + 1 },
          end: { x: last.startX + last.text.length - 1, y: last.row + 1 },
        },
        text: url,
        activate: (_event, text) => {
          window.open(text, '_blank', 'noopener,noreferrer');
        },
      });
    }

    callback(links.length > 0 ? links : undefined);
  }
}

export function registerWrappedUrlLinks(terminal: Terminal): void {
  terminal.registerLinkProvider(new WrappedUrlLinkProvider(terminal));
}
