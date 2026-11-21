/** Keep tab-title formatting available without loading the review renderer. */
export function materialTabTitle(title: string, max = 24): string {
  const text = (title || '').trim() || '(untitled)'
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}
