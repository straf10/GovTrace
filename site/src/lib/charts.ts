// Build-time inline SVG helpers (UI_UX_PLAN.md §5, Sprint B decision:
// build-time SVG αντί για Observable Plot στον client -- βλ. docs/MEMORY.md
// session 6 για το σκεπτικό/μελλοντικό επανέλεγχο).
//
// Καμία client-side βιβλιοθήκη γραφημάτων· αυτές οι συναρτήσεις τρέχουν
// ΜΟΝΟ στο Astro build και επιστρέφουν έτοιμο SVG markup string.

export interface YearPoint {
  year: number;
  value: number | null;
}

export interface Histogram {
  edges: number[];
  counts: number[];
  median: number | null;
  n: number;
}

export function sparklineSvg(points: YearPoint[], width = 140, height = 36): string {
  const pad = 3;
  const defined = points.filter((p) => p.value !== null && p.value !== undefined) as { year: number; value: number }[];
  if (defined.length < 2) {
    return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="ανεπαρκή δεδομένα για γράφημα εξέλιξης"></svg>`;
  }
  const minV = Math.min(...defined.map((p) => p.value));
  const maxV = Math.max(...defined.map((p) => p.value));
  const span = maxV - minV || 1;
  const n = defined.length;
  const coords = defined.map((p, i) => {
    const x = pad + (i / (n - 1)) * (width - 2 * pad);
    const y = height - pad - ((p.value - minV) / span) * (height - 2 * pad);
    return [x, y] as const;
  });
  const d = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lastX, lastY] = coords[coords.length - 1];
  return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="εξέλιξη ${defined[0].year}-${defined[n - 1].year}">` +
    `<path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.5" />` +
    `<circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="2.2" fill="var(--accent)" /></svg>`;
}

export interface YearSplit {
  year: number;
  direct: number;
  total: number;
}

export function stackedValueSvg(rows: YearSplit[], width = 320, height = 90): string {
  if (!rows.length) {
    return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="ανεπαρκή δεδομένα"></svg>`;
  }
  const maxTotal = Math.max(...rows.map((r) => r.total), 1);
  const barW = width / rows.length;
  const usableH = height - 16;
  const bars = rows
    .map((r, i) => {
      const x = i * barW + 2;
      const w = barW - 4;
      const totalH = (r.total / maxTotal) * usableH;
      const directH = (r.direct / maxTotal) * usableH;
      const compH = totalH - directH;
      const yTotalTop = usableH - totalH;
      const yDirectTop = usableH - directH;
      const label = `<text x="${(x + w / 2).toFixed(1)}" y="${height - 2}" font-size="8" text-anchor="middle" fill="var(--muted)">${r.year}</text>`;
      return (
        `<rect x="${x.toFixed(1)}" y="${yTotalTop.toFixed(1)}" width="${w.toFixed(1)}" height="${compH.toFixed(1)}" fill="var(--blue-1)" />` +
        `<rect x="${x.toFixed(1)}" y="${yDirectTop.toFixed(1)}" width="${w.toFixed(1)}" height="${directH.toFixed(1)}" fill="var(--blue-3)" />` +
        label
      );
    })
    .join("");
  return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="αξία αναθέσεων ανά έτος, απευθείας έναντι ανταγωνιστικών">${bars}</svg>`;
}

export function distributionStripSvg(hist: Histogram, value: number | null, width = 220, height = 46): string {
  if (!hist || !hist.counts.length) {
    return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="ανεπαρκή δεδομένα για κατανομή"></svg>`;
  }
  const maxCount = Math.max(...hist.counts, 1);
  const barW = width / hist.counts.length;
  const barsHeight = height - 8;
  const bars = hist.counts
    .map((c, i) => {
      const h = (c / maxCount) * barsHeight;
      const x = i * barW;
      const y = barsHeight - h;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(barW - 1).toFixed(1)}" height="${h.toFixed(1)}" fill="var(--blue-2)" />`;
    })
    .join("");

  const lo = hist.edges[0];
  const hi = hist.edges[hist.edges.length - 1];
  let marker = "";
  if (value !== null && value !== undefined && hi > lo) {
    const clamped = Math.min(Math.max(value, lo), hi);
    const mx = ((clamped - lo) / (hi - lo)) * width;
    marker = `<line x1="${mx.toFixed(1)}" y1="0" x2="${mx.toFixed(1)}" y2="${barsHeight}" stroke="var(--accent)" stroke-width="2" />` +
      `<circle cx="${mx.toFixed(1)}" cy="${barsHeight}" r="2.5" fill="var(--accent)" />`;
  }
  return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="κατανομή δείκτη στην ομάδα σύγκρισης, N=${hist.n}">` +
    `${bars}${marker}</svg>`;
}
