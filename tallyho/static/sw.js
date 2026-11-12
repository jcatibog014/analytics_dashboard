// Tally Ho!'s service worker. It makes the app installable on a phone and keeps
// the look (styles, scripts, fonts, icons) ready on a slow connection.
// Pages always come from the network, so the numbers are never old;
// without a connection a small "You're offline" page shows instead.
// Nothing personal is cached: no pages, no numbers, nothing from forms.
"use strict";

const CACHE = "tallyho-v3"; // change the number when these files change
const FILES = [
  "/static/style.css",
  "/static/app.js",
  "/static/charts.js",
  "/static/theme.js",
  "/static/icons/icon-192.png",
  "/static/icons/logo.svg",
  "/static/fonts/inter-variable.woff2",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Throw away the caches of older versions.
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    // A page: always the network; the offline page only when there's none.
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (FILES.includes(new URL(request.url).pathname)) {
    // Our own styles, scripts, fonts and icons: the network first (so updates show), the cache as a backup.
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request)),
    );
  }
});
