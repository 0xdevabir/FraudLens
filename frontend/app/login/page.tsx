"use client";

import { useState } from "react";

import { Brand } from "@/components/brand";
import { Button, ErrorNote, inputClass } from "@/components/ui";
import { api, setToken, useApi } from "@/lib/api";

type DemoAccount = { username: string; display_name: string; role: string };

/** Only same-site paths are followed after login, so a crafted link cannot send the user elsewhere. */
function safeNext(): string {
  const next = new URLSearchParams(window.location.search).get("next");
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/";
}

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // Empty unless the API runs with FRAUDLENS_DEMO_LOGIN, which production refuses.
  const demo = useApi<{ accounts: DemoAccount[] }>("/v1/auth/demo").data?.accounts ?? [];

  async function signIn(path: string, body: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      const { access_token } = await api<{ access_token: string }>(path, body);
      setToken(access_token);
      window.location.assign(safeNext());
    } catch (problem) {
      setError(problem as Error);
      setBusy(false);
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    void signIn("/v1/auth/login", { username: username.trim(), password });
  }

  return (
    <main className="relative grid min-h-screen place-items-center overflow-hidden px-4 py-10">
      {/* Two soft lights behind the glass: sage above, cyan below. */}
      <div aria-hidden="true" className="pointer-events-none absolute -top-40 left-1/2 size-[36rem] -translate-x-1/2 rounded-full bg-accent/20 blur-[120px]" />
      <div aria-hidden="true" className="pointer-events-none absolute -bottom-56 -left-24 size-[28rem] rounded-full bg-info/10 blur-[120px]" />
      <div className="relative w-full max-w-sm animate-rise">
        <div className="mb-7 flex flex-col items-center text-center">
          <h1 className="sr-only">FraudLens</h1>
          <Brand size={48} className="text-[1.75rem]" />
          <p className="mt-3 text-[0.9375rem] text-fg-3">Real-time fraud decisions for mobile money</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-3xl border border-white/12 bg-white/6 p-6 shadow-2xl shadow-black/40 backdrop-blur-2xl">
          <div>
            <label htmlFor="username" className="mb-1.5 block text-xs font-medium text-fg-3">Username</label>
            <input
              id="username" name="username" autoComplete="username" autoFocus required maxLength={64}
              value={username} onChange={(event) => setUsername(event.target.value)}
              className={`${inputClass} w-full`}
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1.5 block text-xs font-medium text-fg-3">Password</label>
            <input
              id="password" name="password" type="password" autoComplete="current-password" required maxLength={256}
              value={password} onChange={(event) => setPassword(event.target.value)}
              className={`${inputClass} w-full`}
            />
          </div>
          {error && <ErrorNote error={error} />}
          <Button type="submit" variant="primary" disabled={busy || !username || !password} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
          {demo.length > 0 && (
            <div className="border-t border-white/10 pt-4">
              <p className="mb-2 text-xs font-medium text-fg-3">Demo sign-in, no password</p>
              <div className="grid grid-cols-2 gap-2">
                {demo.map((account) => (
                  <Button
                    key={account.username} small disabled={busy} title={account.display_name}
                    onClick={() => void signIn("/v1/auth/demo-login", { username: account.username })}
                  >
                    {account.username}
                  </Button>
                ))}
              </div>
            </div>
          )}
          <p className="text-xs leading-relaxed text-fg-4">
            Seeded accounts: <code>analyst1</code> and <code>analyst2</code> review alerts, <code>supervisor1</code> and{" "}
            <code>supervisor2</code> approve freezes, <code>admin</code> sees oversight only. They share the password set as{" "}
            <code>FRAUDLENS_SEED_PASSWORD</code> when the database was seeded.
          </p>
        </form>
      </div>
    </main>
  );
}

