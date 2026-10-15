import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    extractUrlsFromText,
    normalizeDetectedUrl,
    stripAnsiSequences
} from './url-detection.js';

test('extracts a plain direct URL', () => {
    assert.deepEqual(
        extractUrlsFromText('open https://example.com/docs now'),
        ['https://example.com/docs']
    );
});

test('normalizeDetectedUrl strips trailing punctuation', () => {
    assert.equal(normalizeDetectedUrl('https://example.com/a).'), 'https://example.com/a');
    assert.equal(normalizeDetectedUrl('https://example.com/a,'), 'https://example.com/a');
});

test('normalizeDetectedUrl strips trailing table border characters', () => {
    assert.equal(normalizeDetectedUrl('https://example.com/a│'), 'https://example.com/a');
    assert.equal(normalizeDetectedUrl('https://example.com/a|'), 'https://example.com/a');
});

test('normalizeDetectedUrl rejects non-http schemes and garbage', () => {
    assert.equal(normalizeDetectedUrl('javascript:alert(1)'), null);
    assert.equal(normalizeDetectedUrl('ftp://example.com/a'), null);
    assert.equal(normalizeDetectedUrl('not a url'), null);
});

test('does not swallow adjacent box-drawing characters into the URL', () => {
    assert.deepEqual(
        extractUrlsFromText('│https://example.com/a│'),
        ['https://example.com/a']
    );
    assert.deepEqual(
        extractUrlsFromText('| https://example.com/a |'),
        ['https://example.com/a']
    );
});

test('joins a URL wrapped onto a pure-fragment next line', () => {
    const text = 'visit https://example.com/some/very/lo\nng/path?x=1';
    assert.deepEqual(extractUrlsFromText(text), [
        'https://example.com/some/very/lo',
        'https://example.com/some/very/long/path?x=1'
    ]);
});

test('joins a URL wrapped inside a unicode table cell', () => {
    const text = [
        '│ OAuth 登录地址                │',
        '│ https://auth.example.com/logi │',
        '│ n?state=abc123              │',
        '│ 完成后自动返回                │'
    ].join('\n');
    const urls = extractUrlsFromText(text);
    assert.ok(
        urls.includes('https://auth.example.com/login?state=abc123'),
        `expected joined table URL, got ${JSON.stringify(urls)}`
    );
});

test('joins a URL wrapped inside an ascii table cell', () => {
    const text = '| https://example.com/lo |\n| ng/path |';
    const urls = extractUrlsFromText(text);
    assert.ok(urls.includes('https://example.com/long/path'));
});

test('joins a URL wrapped across more than two table rows', () => {
    const text = '│ https://example.com/a │\n│ /b │\n│ /c │';
    const urls = extractUrlsFromText(text);
    assert.ok(urls.includes('https://example.com/a/b/c'));
});

test('does not glue unrelated text lines onto a URL', () => {
    const text = 'https://example.com/a\nhello world';
    const urls = extractUrlsFromText(text);
    assert.deepEqual(urls, ['https://example.com/a']);
});

test('does not glue a table row with extra cell content onto a URL', () => {
    const text = '│ https://example.com/a │\n│ /b │ 这是说明文字 │';
    const urls = extractUrlsFromText(text);
    assert.ok(!urls.some((url) => url.includes('/a/b')));
});

test('strips ANSI sequences before detection', () => {
    const text = stripAnsiSequences('[32mhttps://example.com/green[0m');
    assert.deepEqual(extractUrlsFromText(text), ['https://example.com/green']);
});
