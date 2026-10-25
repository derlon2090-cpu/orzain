import { NextRequest, NextResponse } from 'next/server';

export function proxy(request: NextRequest) {
  const random = crypto.getRandomValues(new Uint8Array(16));
  const nonce = btoa(String.fromCharCode(...Array.from(random)));
  const websocketSource = process.env.NODE_ENV === 'development' ? 'ws:' : 'wss:';
  const developmentEval = process.env.NODE_ENV === 'development' ? " 'unsafe-eval'" : '';
  const csp = [
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${developmentEval}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ${websocketSource}`,
  ].join('; ');

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('Content-Security-Policy', csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = { matcher: '/:path*' };
