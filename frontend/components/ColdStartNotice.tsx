"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Spinner } from "./ui";

const SHOW_AFTER_MS = 3500;      // a warm API answers /health in well under a second
const GIVE_UP_AFTER_MS = 120000;

/**
 * The public demo runs on free hosting that sleeps after 15 idle minutes, and the first request then takes up to
 * a minute. Without a word of explanation that looks like a broken page, so say what is happening while it wakes.
 */
export default function ColdStartNotice() {
  const [state, setState] = useState<"quiet" | "waking" | "failed">("quiet");

  useEffect(() => {
    let done = false;
    const show = setTimeout(() => !done && setState("waking"), SHOW_AFTER_MS);
    const giveUp = setTimeout(() => !done && setState("failed"), GIVE_UP_AFTER_MS);
    const finish = (next: "quiet" | "failed") => {
      done = true;
      clearTimeout(show);
      clearTimeout(giveUp);
      setState(next);
    };
    // One retry: the very first request to a sleeping instance is sometimes dropped while it boots.
    api.health()
      .catch(() => new Promise((r) => setTimeout(r, 5000)).then(() => api.health()))
      .then(() => finish("quiet"))
      .catch(() => finish("failed"));
    return () => {
      done = true;
      clearTimeout(show);
      clearTimeout(giveUp);
    };
  }, []);

  if (state === "quiet") return null;
  return (
    <div role="status" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900">
      {state === "waking" ? (
        <span className="inline-flex items-center gap-2">
          <Spinner />
          Waking the demo server. It runs on free hosting and sleeps when idle, so the first load can take up to a minute.
        </span>
      ) : (
        <span>
          The demo server is not answering yet. Give it a minute and{" "}
          <button type="button" className="font-semibold underline" onClick={() => location.reload()}>reload the page</button>.
        </span>
      )}
    </div>
  );
}
