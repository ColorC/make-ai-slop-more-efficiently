/** Exchange an existing credential through a site's normal session endpoint.
 * The managed request context receives HttpOnly cookies; callers never export
 * cookies or include credentials in URLs, action records, or error messages.
 */
export async function bootstrapBrowserSession(
  context: { request: { post: (url: string, options: unknown) => Promise<any> } },
  currentUrl: string,
  input: { url: string; bearerToken: string },
): Promise<{ origin: string; pathname: string; status: number }> {
  let current: URL, target: URL;
  try { current = new URL(currentUrl); target = new URL(input.url); }
  catch { throw new Error('Session bootstrap requires a valid current page and endpoint.'); }
  if (!['http:', 'https:'].includes(current.protocol)
      || target.origin !== current.origin || target.username || target.password
      || target.hash || target.search) {
    throw new Error('Session bootstrap endpoint must share the current HTTP(S) page origin and contain no URL credentials or query.');
  }
  if (typeof input.bearerToken !== 'string' || !input.bearerToken.trim()
      || /[\r\n]/.test(input.bearerToken)) {
    throw new Error('Session bootstrap requires a valid bearer credential.');
  }
  let response: any;
  try {
    response = await context.request.post(target.href, {
      headers: { Authorization: `Bearer ${input.bearerToken}`, Origin: current.origin },
      data: {}, maxRedirects: 0, timeout: 15_000,
    });
  } catch { throw new Error('Session bootstrap request failed.'); }
  const status = response.status();
  await response.dispose().catch(() => undefined);
  if (status < 200 || status >= 300) throw new Error(`Session bootstrap rejected (HTTP ${status}).`);
  return { origin: target.origin, pathname: target.pathname, status };
}
