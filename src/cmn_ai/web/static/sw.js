// cmn·ai service worker — makes the app installable and resilient offline.
// Navigations are network-first (so UI updates are never masked by the cache);
// static assets are cache-first (they are versioned with ?v=<mtime>). API calls
// are never cached.
"use strict";

const CACHE = "cmn-ai-v1";
const SHELL = ["/", "/static/chat.css", "/static/chat.js", "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(SHELL).catch(() => undefined))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/api/")) return; // live data, never cached

  if (req.mode === "navigate") {
    // network-first: always try fresh HTML, fall back to cached shell offline
    event.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }

  // cache-first for versioned static assets
  event.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req).then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return resp;
        })
    )
  );
});
