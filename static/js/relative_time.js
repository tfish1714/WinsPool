/**
 * relative_time.js -- Human-friendly "how long ago" labels for the admin panel.
 *
 * Timestamps are Unix seconds (as stored in Firestore). `nowMs` is injectable
 * so the formatter is deterministic under test.
 */

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;
const FRESH_WINDOW_S = 15 * MINUTE; // matches the server-side activity throttle

function toSeconds(ts) {
    const n = Number(ts);
    return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * "Just now" (<1m), "Nm ago" (<60m), "Nh ago" (<24h), "Nd ago" (<7d),
 * otherwise an absolute date such as "Sep 18, 2026". Returns null when the
 * timestamp is missing or invalid. A timestamp slightly in the future
 * (client/server clock skew) reads as "Just now".
 */
export function formatRelativeTime(ts, nowMs = Date.now()) {
    const seconds = toSeconds(ts);
    if (seconds === null) return null;

    const age = Math.max(0, Math.floor(nowMs / 1000 - seconds));
    if (age < MINUTE) return 'Just now';
    if (age < HOUR) return `${Math.floor(age / MINUTE)}m ago`;
    if (age < DAY) return `${Math.floor(age / HOUR)}h ago`;
    if (age < 7 * DAY) return `${Math.floor(age / DAY)}d ago`;
    return new Date(seconds * 1000).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
    });
}

/** True when the timestamp is under 15 minutes old (the "active now" dot). */
export function isFresh(ts, nowMs = Date.now()) {
    const seconds = toSeconds(ts);
    if (seconds === null) return false;
    return nowMs / 1000 - seconds < FRESH_WINDOW_S;
}
