// Notifications card on the player's own page: enable push, choose weekly pushes.
import { getAuthHeaders, STORAGE_KEYS } from './auth_service.js';
import { IOS_HINT_TEXT } from './ios_push_hint.js';
import { currentPushSupport, subscribeToPush } from './push_client.js';

const STATUS_URL = '/api/profile/push-status';
const PREFS_URL = '/api/profile/push-prefs';
const BLOCKED_TEXT = 'Notifications are blocked in your browser settings';
const UNAVAILABLE_TEXT = 'Push notifications are not available right now.';

const el = (id) => document.getElementById(id);

async function fetchStatus() {
    const res = await fetch(STATUS_URL, { headers: getAuthHeaders(), credentials: 'same-origin' });
    if (!res.ok) throw new Error(`push-status ${res.status}`);
    return res.json();
}

function setPrefsEnabled(enabled) {
    el('push-pref-recap').disabled = !enabled;
    el('push-pref-standings').disabled = !enabled;
}

function render(support, status, note) {
    const statusEl = el('push-status');
    const enableBtn = el('push-enable-btn');
    const iosHint = el('push-ios-hint');
    enableBtn.hidden = true;
    iosHint.hidden = true;
    setPrefsEnabled(false);
    if (status) {
        el('push-pref-recap').checked = !!status.prefs.recap;
        el('push-pref-standings').checked = !!status.prefs.standings;
    }
    if (note) {
        statusEl.textContent = note;
    } else if (support === 'ios-needs-install') {
        statusEl.textContent = 'Notifications need the app on your Home Screen.';
        iosHint.textContent = IOS_HINT_TEXT;
        iosHint.hidden = false;
    } else if (support === 'unsupported') {
        statusEl.textContent = 'This browser does not support push notifications.';
    } else if (status && status.subscribed) {
        statusEl.textContent = 'Notifications are on for this account.';
        setPrefsEnabled(true);
    } else if (status && !status.configured) {
        statusEl.textContent = UNAVAILABLE_TEXT;
    } else if (typeof Notification !== 'undefined' && Notification.permission === 'denied') {
        statusEl.textContent = BLOCKED_TEXT;
    } else {
        statusEl.textContent = 'Notifications are off.';
        enableBtn.hidden = false;
    }
}

let started = false;
async function start() {
    if (started) return;
    started = true;
    el('notifications').hidden = false;
    // The nudge deep-links here, but the block was hidden when the browser tried to jump.
    if (location.hash === '#notifications') el('notifications').scrollIntoView();
    const support = currentPushSupport();
    let status = null;
    try {
        status = await fetchStatus();
    } catch (e) {
        render(support, null, 'Could not load notification settings.');
        return;
    }
    render(support, status);

    el('push-enable-btn').addEventListener('click', async () => {
        const meta = document.querySelector('meta[name="vapid-public-key"]');
        const vapidKey = meta && meta.content;
        let pid = NaN;
        try { pid = parseInt(localStorage.getItem(STORAGE_KEYS.PLAYER_ID), 10); } catch (e) { /* ignore */ }
        if (!vapidKey || !pid) { render(support, status, UNAVAILABLE_TEXT); return; }
        const result = await subscribeToPush(pid, vapidKey);
        try { status = await fetchStatus(); } catch (e) { /* keep prior status */ }
        if (result === 'denied') render(support, status, BLOCKED_TEXT);
        else if (result === 'error') render(support, status, 'Could not turn on notifications. Please try again.');
        else render(support, status);
    });

    const savePrefs = async (evt) => {
        try {
            const res = await fetch(PREFS_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                credentials: 'same-origin',
                body: JSON.stringify({
                    recap: el('push-pref-recap').checked,
                    standings: el('push-pref-standings').checked,
                }),
            });
            if (!res.ok) throw new Error(`push-prefs ${res.status}`);
        } catch (e) {
            // Revert the toggle that failed so the UI matches what is stored.
            evt.target.checked = !evt.target.checked;
        }
    };
    el('push-pref-recap').addEventListener('change', savePrefs);
    el('push-pref-standings').addEventListener('change', savePrefs);
}

function maybeStart() {
    const own = el('own-page-only');
    if (!own) return;
    if (!own.hidden) { start(); return; }
    // The page's inline script reveals the block after this module may have run.
    new MutationObserver((_m, obs) => {
        if (!own.hidden) { obs.disconnect(); start(); }
    }).observe(own, { attributes: true, attributeFilter: ['hidden'] });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', maybeStart);
} else {
    maybeStart();
}
