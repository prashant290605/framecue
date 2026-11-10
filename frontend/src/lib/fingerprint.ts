/** Content fingerprint of an upload: sha256 over the files' own hashes (order and names
 *  ignored) plus the AI bundle's hash. Computed in the browser; used only to spot an analysis
 *  that is already saved. Returns null where Web Crypto is unavailable. */
export async function fingerprint(files: File[], bundle: File | null): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  try {
    const hex = async (data: BufferSource) =>
      [...new Uint8Array(await subtle.digest("SHA-256", data))].map((b) => b.toString(16).padStart(2, "0")).join("");
    const parts = (await Promise.all(files.map(async (f) => hex(await f.arrayBuffer())))).sort();
    const extra = bundle ? await hex(await bundle.arrayBuffer()) : "none";
    return await hex(new TextEncoder().encode(JSON.stringify({ files: parts, bundle: extra })));
  } catch {
    return null;
  }
}
