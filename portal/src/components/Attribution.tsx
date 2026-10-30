import type { Attribution as Source } from "../api/types";

/** The price source beside a chart or price table. A followed link (no
rel="nofollow"), as Twelve Data's attribution guidelines require. */
export function Attribution({ source, children }: { source: Source | null; children?: React.ReactNode }) {
  if (source === null) return null;
  const { text, link, url } = source;
  const at = link === null ? -1 : text.indexOf(link);
  return (
    <p className="attribution">
      {link === null || url === null || at < 0 ? (
        text
      ) : (
        <>
          {text.slice(0, at)}
          <a href={url} target="_blank" rel="noopener">
            {link}
          </a>
          {text.slice(at + link.length)}
        </>
      )}
      {children}
    </p>
  );
}
