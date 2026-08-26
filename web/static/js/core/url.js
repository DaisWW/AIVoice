const ALLOWED_PROTOCOLS = new Set(["http:", "https:"]);

export function safeResourceUrl(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw, window.location.origin);
    if (!ALLOWED_PROTOCOLS.has(url.protocol) || url.origin !== window.location.origin) return "";
    return url.href;
  } catch (_) {
    return "";
  }
}
