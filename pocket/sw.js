/* MoneyPilot Pocket service worker — caches the app shell so the phone can open
 * and log with no signal. Bump CACHE on any shell change to refresh clients. */
const CACHE = "mp-pocket-v1";
const SHELL = [
  "./", "./index.html", "./pocket.css", "./pocket.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png", "./icons/icon-512.png", "./icons/apple-touch-icon.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  // Never cache sync POSTs to the home desktop — they must hit the network.
  if (req.method !== "GET" || req.url.includes("/pocket/sync")) return;
  // App shell: cache-first (works offline); fall back to network for the rest.
  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).catch(() => caches.match("./index.html")))
  );
});
