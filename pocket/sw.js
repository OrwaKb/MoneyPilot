/* MoneyPilot Pocket service worker — caches the app shell so the phone can open
 * and log with no signal. Bump CACHE on any shell change to refresh clients. */
const CACHE = "mp-pocket-v3";
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
  // Never touch sync POSTs to the home desktop — they must hit the network.
  if (req.method !== "GET" || req.url.includes("/pocket/")) return;
  // Network-FIRST so app updates land immediately for returning users; cache the
  // fresh copy and fall back to it (then index.html) only when offline.
  e.respondWith(
    fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || caches.match("./index.html")))
  );
});
