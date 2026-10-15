// 离线壳（网络优先版）：
//  - 有网时所有资源都走网络，保证「一更新就生效」；断网时回退缓存（工地可只读查看）
//  - 记录/汇总/历史/图片同样网络优先、断网回退缓存
//  - 写操作永远走网络
const CACHE = 'costmem-v4';
const SHELL = ['/', '/index.html', '/style.css', '/app.js', '/manifest.json', '/icon.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(req) {
  try {
    const resp = await fetch(req);
    if (resp && resp.ok) {
      const copy = resp.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
    }
    return resp;
  } catch (err) {
    const hit = await caches.match(req);
    if (hit) return hit;
    if (req.mode === 'navigate') {
      const shell = await caches.match('/index.html');
      if (shell) return shell;
    }
    throw err;
  }
}

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;      // 写操作永远走网络
  if (url.pathname.startsWith('/api/')
      && url.pathname !== '/api/records'
      && url.pathname !== '/api/summary'
      && url.pathname !== '/api/history'
      && !url.pathname.startsWith('/api/photo/')) {
    return;                                     // 登录/导出等不缓存
  }
  e.respondWith(networkFirst(e.request));
});
