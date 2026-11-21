export declare function wikiRefAttr(page: string): string;
export declare function installWikiDevMode(opts: {
  isEnabled: () => boolean;
  onOpen: (ref: string, el: Element) => void;
  root?: Document | Element;
}): { sync: () => void; uninstall: () => void };
