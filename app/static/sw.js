const CACHE_NAME = "qas-pwa-v1";
const STATIC_CACHE = [
  "/",
  "/login",
  "/static/css/bootstrap.min.css",
  "/static/css/bootstrap-icons.min.css",
  "/static/css/dashboard.css",
  "/static/css/ios-glass.css",
  "/static/js/jquery-3.5.1.slim.min.js",
  "/static/js/bootstrap.bundle.min.js",
  "/static/js/vue@2.js",
  "/static/js/axios.min.js",
  "/static/js/v-jsoneditor.min.js",
  "/static/js/pwa-register.js",
  "/static/favicon.ico",
  "/static/img/pwa-192.svg",
  "/static/img/pwa-512.svg",
  "/manifest.webmanifest"
];

const API_PREFIXES = [
  "/data",
  "/update",
  "/run_script_now",
  "/resource_search",
  "/gying_second_layer",
  "/shareurl_check"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_CACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (API_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) return;

  if (
    url.pathname.startsWith("/static/") ||
    url.pathname === "/manifest.webmanifest" ||
    url.pathname === "/favicon.ico"
  ) {
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req).then((res) => {
        const clone = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
        return res;
      }))
    );
    return;
  }

  event.respondWith(
    fetch(req)
      .then((res) => {
        const clone = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
        return res;
      })
      .catch(() => caches.match(req).then((cached) => cached || caches.match("/login")))
  );
});
