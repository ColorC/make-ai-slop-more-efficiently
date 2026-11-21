const ANSI_ESCAPE_SEQUENCE_REGEX = /\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))/g;
// `|` is never legal unencoded in a URL (RFC 3986); box-drawing chars never are.
// Both appear as table borders in CLI output and must not stick to matched URLs.
const TRAILING_URL_PUNCTUATION_REGEX = /[)\]}>.,;:!?|\u2500-\u257F]+$/;
// Direct URL match: stop at whitespace, quotes, backslash, ANSI, `|` and box-drawing chars.
const URL_MATCH_REGEX = /https?:\/\/[^\s<>"'`\\\x1b\x07|\u2500-\u257F]+/gi;
const URL_START_REGEX = /https?:\/\/[^\s<>"'`\\\x1b\x07|\u2500-\u257F]+/i;
// A continuation fragment is a pure run of URL characters.
const URL_FRAGMENT_REGEX = /^[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$/;
// A table row holding only a URL fragment: optional border/space padding around
// a single run of URL characters (e.g. `│ /path/continued │` or `| foo=1 |`).
const TABLE_FRAGMENT_ROW_REGEX = /^[\s|\u2500-\u257F]*([A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)[\s|\u2500-\u257F]*$/;

function stripAnsiSequences(value = '') {
    return value.replace(ANSI_ESCAPE_SEQUENCE_REGEX, '');
}

function normalizeDetectedUrl(url) {
    if (!url || typeof url !== 'string') return null;

    const cleaned = url.trim().replace(TRAILING_URL_PUNCTUATION_REGEX, '');
    if (!cleaned) return null;

    try {
        const parsed = new URL(cleaned);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return null;
        }
        return parsed.toString();
    } catch {
        return null;
    }
}

/**
 * Extract the continuation fragment from the line after a wrapped URL.
 * Handles both plain hard-wraps (the whole line is URL characters) and URLs
 * wrapped inside table cells (border/space padding around the fragment).
 * Returns null when the line carries any other content — continuing there
 * would glue unrelated text onto the URL.
 */
function matchContinuationFragment(rawLine) {
    const trimmed = rawLine.trim();
    if (!trimmed) return null;
    if (URL_FRAGMENT_REGEX.test(trimmed)) return trimmed;
    const tableMatch = rawLine.match(TABLE_FRAGMENT_ROW_REGEX);
    if (tableMatch) return tableMatch[1];
    return null;
}

function extractUrlsFromText(value = '') {
    const directMatches = value.match(URL_MATCH_REGEX) || [];

    // Handle wrapped terminal URLs split across lines by terminal width or
    // clipped into narrow table cells.
    const wrappedMatches = [];
    const lines = value.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
        const startMatch = lines[i].match(URL_START_REGEX);
        if (!startMatch) continue;

        let combined = startMatch[0];
        let j = i + 1;
        while (j < lines.length) {
            const fragment = matchContinuationFragment(lines[j]);
            if (fragment === null) break;
            combined += fragment;
            j++;
        }

        wrappedMatches.push(combined);
    }

    return Array.from(new Set([...directMatches, ...wrappedMatches]));
}

function shouldAutoOpenUrlFromOutput(value = '') {
    const normalized = value.toLowerCase();
    return (
        normalized.includes('browser didn\'t open') ||
        normalized.includes('open this url') ||
        normalized.includes('continue in your browser') ||
        normalized.includes('press enter to open') ||
        normalized.includes('open_url:')
    );
}

export {
    ANSI_ESCAPE_SEQUENCE_REGEX,
    TRAILING_URL_PUNCTUATION_REGEX,
    stripAnsiSequences,
    normalizeDetectedUrl,
    extractUrlsFromText,
    shouldAutoOpenUrlFromOutput
};
