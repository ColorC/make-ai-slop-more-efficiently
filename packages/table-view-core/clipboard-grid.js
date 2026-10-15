/** Return whether plain clipboard text represents more than one grid coordinate. */
export function isRectangularClipboardText(text) {
  return text.includes("\t") || text.includes("\n") || text.includes("\r");
}

/** Parse the plain-text clipboard shape produced by Excel and other grid tools. */
export function parseClipboardTsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;

  const pushCell = () => {
    row.push(cell);
    cell = "";
  };
  const pushRow = () => {
    pushCell();
    rows.push(row);
    row = [];
  };

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === '"') {
      if (quoted && text[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else if (quoted || cell.length === 0) {
        quoted = !quoted;
      } else {
        cell += character;
      }
      continue;
    }
    if (!quoted && character === "\t") {
      pushCell();
      continue;
    }
    if (!quoted && (character === "\r" || character === "\n")) {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      pushRow();
      continue;
    }
    cell += character;
  }
  pushRow();

  if (/\r?\n$/.test(text) && rows.at(-1)?.length === 1 && rows.at(-1)?.[0] === "") rows.pop();
  const width = Math.max(1, ...rows.map((values) => values.length));
  return rows.map((values) => Array.from({ length: width }, (_, index) => values[index] ?? ""));
}
