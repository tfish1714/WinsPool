self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : { title: 'WinsPool', body: "You're on the clock!" };
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/fishbone.png',
            badge: '/static/fishbone.png',
        })
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(clients.openWindow('/'));
});
