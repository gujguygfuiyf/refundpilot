"use client";

import { useEffect, useState } from "react";

/** Drag-and-drop or click to pick 1–3 photos, with previews. */
export default function PhotoPicker({ files, onChange, max = 3 }: { files: File[]; onChange: (f: File[]) => void; max?: number }) {
  const [previews, setPreviews] = useState<string[]>([]);
  const [over, setOver] = useState(false);

  useEffect(() => {
    const urls = files.map((f) => URL.createObjectURL(f));
    setPreviews(urls);
    return () => urls.forEach((u) => URL.revokeObjectURL(u));
  }, [files]);

  const add = (list: FileList | null) => {
    if (!list) return;
    const imgs = Array.from(list).filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name));
    onChange([...files, ...imgs].slice(0, max));
  };

  return (
    <div>
      <label
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center text-sm transition ${
          over ? "border-brand bg-blue-50" : "border-gray-300 bg-gray-50 hover:border-gray-400"
        } ${files.length >= max ? "pointer-events-none opacity-50" : ""}`}
      >
        <span className="font-medium text-gray-800">Drop photos here or click to choose</span>
        <span className="mt-1 text-gray-500">Up to {max} photos. Show the item and the problem clearly.</span>
        <input type="file" accept="image/*,.heic,.heif" multiple className="hidden" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </label>
      {files.length > 0 && (
        <div className="mt-3 grid grid-cols-3 gap-3">
          {previews.map((src, i) => (
            <div key={src} className="group relative aspect-square overflow-hidden rounded-lg bg-gray-100 ring-1 ring-gray-200">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={src} alt={`Photo ${i + 1}`} className="h-full w-full object-cover" />
              <button
                type="button"
                onClick={() => onChange(files.filter((_, j) => j !== i))}
                className="absolute top-1 right-1 rounded-full bg-black/60 px-2 py-0.5 text-xs text-white opacity-0 transition group-hover:opacity-100"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
