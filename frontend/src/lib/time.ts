/** Exact screenplay timecode, matching the backend: MM:SS.cc (or MM:SS.mmm when the value is
 *  not a whole centisecond). Integer milliseconds in, no rounding. */
export function timecode(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor(ms / 1000) % 60;
  const frac = ms % 1000;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  const ff = frac % 10 === 0 ? String(frac / 10).padStart(2, "0") : String(frac).padStart(3, "0");
  return `${mm}:${ss}.${ff}`;
}

/** Duration in seconds with centisecond precision, e.g. "38.29 s". */
export function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(2)} s`;
}

/** Short axis label, e.g. "1:05". */
export function axisLabel(ms: number): string {
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Decimal units, matching the API's documented limits (4.0 MB = 4,000,000 bytes). */
export function bytes(n: number): string {
  if (n < 1000) return `${n} B`;
  if (n < 1_000_000) return `${Math.round(n / 1000)} KB`;
  return `${(n / 1_000_000).toFixed(1)} MB`;
}
