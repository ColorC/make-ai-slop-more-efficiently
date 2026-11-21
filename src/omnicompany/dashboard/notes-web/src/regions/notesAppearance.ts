export type NotesAppearance = "omni" | "workshop";

const WORKSHOP_BG = "#F7F3EB";
const OMNI_BG = "#0b1830";

export function resolveNotesAppearance(
  search = typeof window === "undefined" ? "" : window.location.search,
): NotesAppearance {
  const requested = new URLSearchParams(search).get("theme") || "";
  if (requested === "workshop" || requested === "warm") return "workshop";
  if (typeof document !== "undefined" && document.documentElement.dataset.notesTheme === "workshop") {
    return "workshop";
  }
  return "omni";
}

export function applyNotesAppearance(
  theme: NotesAppearance = resolveNotesAppearance(),
): NotesAppearance {
  if (typeof document === "undefined") return theme;
  const root = document.documentElement;
  root.dataset.notesTheme = theme;
  root.style.colorScheme = theme === "workshop" ? "light" : "dark";
  const bg = theme === "workshop" ? WORKSHOP_BG : OMNI_BG;
  root.style.background = bg;
  if (document.body) document.body.style.background = bg;
  return theme;
}

export function notesReviewEnabled(
  appearance: NotesAppearance = resolveNotesAppearance(),
): boolean {
  return appearance === "omni";
}
