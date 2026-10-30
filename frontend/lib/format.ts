export function money(cents: number | null | undefined, currency = "USD"): string {
  if (cents === null || cents === undefined) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);
}

export function label(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

/** Downscale to a JPEG with the long edge <= maxEdge, matching the backend's 1568px limit. */
export async function compressImage(file: File, maxEdge = 1568, quality = 0.85): Promise<Blob> {
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode failed"))), "image/jpeg", quality),
    );
  } catch {
    return file; // browser can't decode it (e.g. HEIC): let the server handle it
  }
}
