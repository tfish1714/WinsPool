self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : { title: 'WinsPool', body: "You're on the clock!" };
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/fishbone.png',
            badge: '/static/fishbone.png',
            data: { url: data.url || '/' },
        })
    );
});

function _safeTarget(url) {
    try {
        const u = new URL(url || '/', self.location.origin);
        return u.origin === self.location.origin ? u.pathname + u.search + u.hash : '/';
    } catch (e) {
        return '/';
    }
}

self.addEventListener('notificationclick', event => {
    event.notification.close();
    const target = _safeTarget(event.notification.data && event.notification.data.url);
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
            for (const c of list) {
                if (typeof c.navigate !== 'function') continue;
                return Promise.resolve()
                    .then(() => c.focus())
                    .then(() => c.navigate(target))
                    .catch(() => clients.openWindow(target));
            }
            return clients.openWindow(target);
        })
    );
});
