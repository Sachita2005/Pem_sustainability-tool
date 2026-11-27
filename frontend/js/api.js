// Talks to the backend. GET responses are cached for the life of the page
// (the source workbooks do not change while someone is looking at a chart);
// pass { fresh: true } after a save, or call clearCache().

const cache = new Map();

function url(path, params) {
  const q = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') q.set(k, v);
  });
  const qs = q.toString();
  return '/api' + path + (qs ? '?' + qs : '');
}

async function request(method, path, { params, body, fresh } = {}) {
  const target = url(path, params);
  if (method === 'GET' && !fresh && cache.has(target)) return cache.get(target);

  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(target, init);
  } catch (err) {
    throw new Error('The server could not be reached. Check that the app is running.');
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (err) { data = text; }
  if (!res.ok) {
    const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : res.statusText;
    const error = new Error(detail || 'Request failed (' + res.status + ')');
    error.status = res.status;
    error.detail = data && data.detail !== undefined ? data.detail : null;   // structured detail (e.g. 422 field errors)
    throw error;
  }
  if (method === 'GET') cache.set(target, data);
  return data;
}

export const api = {
  get: (path, params, opts) => request('GET', path, { params, ...(opts || {}) }),
  post: (path, body, params) => request('POST', path, { body, params }),
  put: (path, body, params) => request('PUT', path, { body, params }),
  del: (path, params) => request('DELETE', path, { params }),
  url,
  // Drop cached GETs whose URL starts with /api + prefix (all of them when no prefix is given).
  clearCache(prefix) {
    if (!prefix) { cache.clear(); return; }
    [...cache.keys()].filter(k => k.startsWith('/api' + prefix)).forEach(k => cache.delete(k));
  }
};
