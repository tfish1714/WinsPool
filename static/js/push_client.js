// Shared Web Push client: support detection, nudge gating and the subscribe flow.
// pushSupportState / shouldShowPushNudge are pure (node-testable); nothing here
// touches window/document at import time.
import { getAuthHeaders } from './auth_service.js';
import { isIosDevice } from './ios_push_hint.js';

export function pushSupportState({
    userAgent = '', platform = '', maxTouchPoints = 0,
    standalone = false, hasServiceWorker = false, hasPushManager = false,
} = {}) {
    // iOS only exposes Web Push to Home Screen installs; in a plain tab the
    // user needs install guidance, not an Enable button.
    if (isIosDevice({ userAgent, platform, maxTouchPoints }) && !standalone) {
        return 'ios-needs-install';
    }
    return hasServiceWorker && hasPushManager ? 'supported' : 'unsupported';
}

export function shouldShowPushNudge({ support, subscribed, permission, dismissed } = {}) {
    return support === 'supported' && !subscribed && permission !== 'denied' && !dismissed;
}

// Reads the live browser environment (call from the page, not at import time).
export function currentPushSupport() {
    const standalone = !!(
        (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
        || navigator.standalone
    );
    return pushSupportState({
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        maxTouchPoints: navigator.maxTouchPoints,
        standalone,
        hasServiceWorker: 'serviceWorker' in navigator,
        hasPushManager: 'PushManager' in window,
    });
}

export function _urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

// Registers the service worker, asks for permission, subscribes and stores the
// subscription. Returns 'granted' | 'denied' | 'error'.
export async function subscribeToPush(playerId, vapidKey) {
    try {
        const reg = await navigator.serviceWorker.register('/sw.js');
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') return 'denied';

        const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: _urlBase64ToUint8Array(vapidKey),
        });

        const res = await fetch('/api/draft/push-subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
            body: JSON.stringify({ playerId, subscription: sub.toJSON() }),
        });
        if (!res.ok) throw new Error(`push-subscribe responded ${res.status}`);
        return 'granted';
    } catch (e) {
        console.warn('[Push] Subscription failed:', e);
        // console.warn alone is invisible once the user closes devtools --
        // report it server-side so it's actually observable in Cloud Logging.
        fetch('/api/push/client-error', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
            body: JSON.stringify({ reason: `${e?.name || 'Error'}: ${e?.message || e}` }),
        }).catch(() => {});
        return 'error';
    }
}
