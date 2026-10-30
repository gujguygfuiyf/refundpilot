"use client";

import { useEffect, useState, type ReactNode } from "react";
import { getMerchantToken, setMerchantToken } from "@/lib/api";
import { Button, Card } from "./ui";

/** Demo-grade merchant login: a single bearer token, kept in this browser. */
export default function TokenGate({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => setToken(getMerchantToken()), []);

  if (token === null) return null;
  if (token) return <>{children}</>;
  return (
    <Card title="Merchant sign-in" className="mx-auto max-w-md">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setMerchantToken(draft.trim());
          setToken(draft.trim());
        }}
        className="space-y-3"
      >
        <p className="text-sm text-gray-600">Enter the merchant demo token from the README.</p>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          type="password"
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          placeholder="Merchant token"
        />
        <Button type="submit" disabled={!draft.trim()}>Continue</Button>
      </form>
    </Card>
  );
}

export function signOut() {
  setMerchantToken("");
  location.reload();
}
