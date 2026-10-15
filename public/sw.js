// 离线壳 + 记录缓存：
//  - 静态资源：缓存优先（离线可打开界面）
//  - 记录/汇总/历史/图片：网络优先，断网回退缓存（工地没信号也能翻看，只读）
//  - 其他 API 与所有写操作：永远走网络
const CACHE = 'costmem-v2';
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

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return; // 写操作永远走网络

  const readable = url.pathname === '/api/records'
    || url.pathname === '/api/summary'
    || url.pathname === '/api/history'
    || url.pathname.startsWith('/api/photo/');

  if (readable) {
    // 网络优先；失败（断网）时回退到上次缓存
    e.respondWith(
      fetch(e.request).then(resp => {
        const copy = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return resp;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  if (url.pathname.startsWith('/api/')) return; // 登录/导出等不缓存

  e.respondWith(
    caches.match(e.request).then(hit => hit || fetch(e.request).then(resp => {
      const copy = resp.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
      return resp;
    }).catch(() => caches.match('/index.html')))
  );
});
