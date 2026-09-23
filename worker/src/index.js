/**
 * News Tracker keywords API -- a small Cloudflare Worker backing the
 * "키워드 관리" (keyword management) screen described in the Nocturne
 * redesign handoff.
 *
 * This is intentionally the *only* piece of the redesign that needs a real
 * server: it is the one place where the browser must durably write data
 * that a later, unrelated process (the hourly GitHub Actions collection
 * run) reads back. Everything else in the redesign (article history,
 * clustering, read/bookmark state) is handled by the existing static-site
 * pipeline + localStorage, which needs no server at all.
 *
 * Storage: a single JSON array under the KV key "keywords" in the
 * KEYWORDS_KV namespace (binding configured in wrangler.toml). Each entry:
 *   { name: string, exclude: string[], naver: bool, google: bool, active: bool }
 *
 * Endpoints:
 *   GET  /api/keywords   -- public read. Returns the current list (or the
 *                            bootstrap default below, if KV is empty --
 *                            this only happens before the very first save).
 *   PUT  /api/keywords   -- replace the whole list. Requires header
 *                            "X-Admin-Key" matching the ADMIN_KEY secret.
 *                            Body: the full JSON array (validated below).
 *
 * Anything else 404s. CORS is restricted to the two sites this API is
 * actually used from, plus localhost for local development.
 */

const KV_KEY = "keywords";

// Mirrors config.ci.yaml's keyword list -- used only until the first PUT
// ever populates KV (i.e. before anyone has opened the keyword management
// screen and saved once).
const DEFAULT_KEYWORDS = [
  "두두원",
  "이음5G",
  "5G 특화망",
  "P5G",
  "방사청 5G",
  "한화시스템",
  "해수부 IoT",
].map((name) => ({ name, exclude: [], naver: true, google: true, active: true }));

const ALLOWED_ORIGINS = new Set([
  "https://news-tracker-7gg.pages.dev",
  "https://jaeholee2.github.io",
]);

function isAllowedOrigin(origin) {
  if (!origin) return false;
  if (ALLOWED_ORIGINS.has(origin)) return true;
  try {
    const { hostname, protocol } = new URL(origin);
    if (protocol === "http:" && (hostname === "localhost" || hostname === "127.0.0.1")) {
      return true; // local dev, any port
    }
  } catch {
    return false;
  }
  return false;
}

function corsHeaders(request) {
  const origin = request.headers.get("Origin") || "";
  const headers = {
    "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
  if (isAllowedOrigin(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

function json(data, status, extraHeaders) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...extraHeaders,
    },
  });
}

/** Validates and normalizes a candidate keyword list from a PUT body.
 * Throws a descriptive Error on the first problem found. */
function validateKeywords(raw) {
  if (!Array.isArray(raw) || raw.length === 0) {
    throw new Error("body must be a non-empty array");
  }
  const seen = new Set();
  return raw.map((item, i) => {
    if (!item || typeof item !== "object") {
      throw new Error(`item ${i} is not an object`);
    }
    const name = typeof item.name === "string" ? item.name.trim() : "";
    if (!name) {
      throw new Error(`item ${i} has an empty name`);
    }
    if (seen.has(name)) {
      throw new Error(`duplicate keyword: ${name}`);
    }
    seen.add(name);

    const exclude = Array.isArray(item.exclude)
      ? item.exclude.map((x) => String(x).trim()).filter(Boolean)
      : [];

    return {
      name,
      exclude,
      naver: item.naver !== false,
      google: item.google !== false,
      active: item.active !== false,
    };
  });
}

async function handleGet(env, request) {
  const stored = await env.KEYWORDS_KV.get(KV_KEY, "json");
  return json(stored || DEFAULT_KEYWORDS, 200, corsHeaders(request));
}

async function handlePut(env, request) {
  const adminKey = request.headers.get("X-Admin-Key") || "";
  const expected = env.ADMIN_KEY || "";
  // Constant-time-ish comparison isn't practical to get exactly right on
  // Workers without extra crypto ceremony; for a single-admin personal
  // tool behind HTTPS this straightforward check is an acceptable
  // trade-off, not a bank-grade auth system.
  if (!expected || adminKey !== expected) {
    return json({ error: "unauthorized" }, 401, corsHeaders(request));
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400, corsHeaders(request));
  }

  let normalized;
  try {
    normalized = validateKeywords(body);
  } catch (exc) {
    return json({ error: String(exc.message || exc) }, 400, corsHeaders(request));
  }

  await env.KEYWORDS_KV.put(KV_KEY, JSON.stringify(normalized));
  return json(normalized, 200, corsHeaders(request));
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    if (url.pathname === "/api/keywords") {
      if (request.method === "GET") return handleGet(env, request);
      if (request.method === "PUT") return handlePut(env, request);
      return json({ error: "method not allowed" }, 405, corsHeaders(request));
    }

    if (url.pathname === "/" || url.pathname === "/health") {
      return json({ ok: true, service: "news-tracker-api" }, 200, corsHeaders(request));
    }

    return json({ error: "not found" }, 404, corsHeaders(request));
  },
};
