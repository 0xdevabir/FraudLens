import { NextResponse, type NextRequest } from "next/server";

const isDev = process.env.NODE_ENV === "development";
/** The only other origin the console talks to. Inlined at build time, like in lib/api.ts. */
const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010").origin;

/**
 * A fresh Content-Security-Policy per page view. Scripts run only with this request's nonce,
 * so markup that reaches the page some other way (a note, a wallet id, a model reason) cannot
 * execute, and the page can send data nowhere but the FraudLens API.
 */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = [
    "default-src 'self'",
    // Development needs eval for React's error overlay; production does not.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' ${isDev ? "'unsafe-inline'" : `'nonce-${nonce}'`}`,
    // Charts and the network graph position themselves with style attributes.
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self' ${apiOrigin}${isDev ? " ws:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp); // Next.js reads the nonce for its own scripts from here
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
