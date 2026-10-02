"use client";

import { useState } from "react";

import { Button, ErrorNote, inputClass } from "@/components/ui";
import { api, setToken } from "@/lib/api";

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

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { access_token } = await api<{ access_token: string }>("/v1/auth/login", { username: username.trim(), password });
      setToken(access_token);
      window.location.assign(safeNext());
    } catch (problem) {
      setError(problem as Error);
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-slate-900 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-white">FraudLens</h1>
          <p className="mt-1 text-sm text-slate-400">Real-time fraud decisions for mobile money</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-lg bg-white p-6 shadow-xl">
          <div>
            <label htmlFor="username" className="mb-1 block text-xs font-medium text-slate-600">Username</label>
            <input
              id="username" name="username" autoComplete="username" autoFocus required maxLength={64}
              value={username} onChange={(event) => setUsername(event.target.value)}
              className={`${inputClass} w-full`}
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-xs font-medium text-slate-600">Password</label>
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
          <p className="text-xs leading-relaxed text-slate-500">
            Seeded accounts: <code>analyst1</code> and <code>analyst2</code> review alerts, <code>supervisor1</code> and{" "}
            <code>supervisor2</code> approve freezes, <code>admin</code> sees oversight only. They share the password set as{" "}
            <code>FRAUDLENS_SEED_PASSWORD</code> when the database was seeded.
          </p>
        </form>
      </div>
    </main>
  );
}
