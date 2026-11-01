export class ApiClientError extends Error {
  constructor({ status = 0, code = 'NETWORK_ERROR', message = 'Request failed', details = {} }) {
    super(message);
    Object.assign(this, { status, code, details });
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// GETs are retried (502/503/504/network). POSTs are never auto-retried; the caller reuses an Idempotency-Key.
export async function request(method, path, { body, idempotencyKey } = {}) {
  const delays = method === 'GET' ? [1000, 3000] : [];
  let attempt = 0;
  for (;;) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: {
          ...(body ? { 'Content-Type': 'application/json' } : {}),
          ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      if (attempt < delays.length) { await sleep(delays[attempt++]); continue; }
      throw new ApiClientError({ message: 'Unable to reach ResQRoute. Check your connection and that the backend is running, then retry.' });
    }
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON body */ }
    if (res.ok) return data;
    if ([502, 503, 504].includes(res.status) && attempt < delays.length) { await sleep(delays[attempt++]); continue; }
    const e = data?.error ?? {};
    throw new ApiClientError({ status: res.status, code: e.code ?? 'HTTP_ERROR', message: e.message ?? `Request failed (${res.status})`, details: e.details ?? {} });
  }
}
export const api = {
  health: () => request('GET', '/api/health'),
  districts: () => request('GET', '/api/geography/haryana/districts'),
  routes: (body, key) => request('POST', '/api/routes', { body, idempotencyKey: key }),
  assess: (body, key) => request('POST', '/api/route-assessments', { body, idempotencyKey: key }),
  ai: (id, key) => request('POST', `/api/route-assessments/${encodeURIComponent(id)}/ai-analysis`, { idempotencyKey: key }),
  weather: (districtId) => request('GET', `/api/weather/districts/${encodeURIComponent(districtId)}`),
  refresh: () => request('POST', '/api/data/refresh'),
};
export const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(16).slice(2)}`);
