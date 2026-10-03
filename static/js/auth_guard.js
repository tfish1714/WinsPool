/**
 * WinsPool client auth guard (classic script, loaded before main.js).
 *
 * The client treats a saved player id as "logged in". If the server later
 * rejects the session token (expired after 7 days, invalid, or revoked by a
 * password change), every protected call 401s and pages show errors until the
 * user signs out and back in by hand. This wraps window.fetch so a rejected
 * session signs the user out cleanly, once, and returns them to the sign-in
 * screen.
 *
 * Safety rules: it only inspects same-origin /api/ responses that are 401s,
 * never touches the request or the response the caller receives, never throws
 * into the caller, and ignores the credential endpoints (a 401 there means
 * wrong credentials, not a dead session).
 */
(function () {
    'use strict';

    if (typeof window === 'undefined' || typeof window.fetch !== 'function') return;
    if (window.__winsPoolAuthGuard) return;
    window.__winsPoolAuthGuard = true;

    var ID_KEY = 'nfl_wins_my_player_id';
    var TOKEN_KEY = 'nfl_wins_token';

    // A 401 from these means wrong credentials / a normal flow, never a dead session.
    var EXCLUDED_PATHS = [
        '/api/login',
        '/api/mfa/verify',
        '/api/set_password',
        '/api/check_player',
        '/api/profile/update',
        '/api/logout'
    ];

    var DEAD_STATES = { expired: 1, invalid: 1, revoked: 1, missing: 1 };
    // Anchored to the exact server strings (services/session_service.py) so an
    // unrelated 401 such as "MFA code expired or invalid." never signs the user out.
    var DEAD_DETAIL = /^\s*(session expired\. please log in again\.|invalid session token\.|missing or invalid authorization header\.|session is no longer valid\. please log in again\.)\s*$/i;

    var origFetch = window.fetch;
    var handled = false; // at most one sign-out per page load

    function hasSavedLogin() {
        try {
            var id = window.localStorage.getItem(ID_KEY);
            var tok = window.localStorage.getItem(TOKEN_KEY);
            var idOk = id && id !== 'null' && id !== 'undefined';
            return !!(idOk || tok);
        } catch (e) {
            return false;
        }
    }

    // Returns the pathname if `input` is a same-origin request, else null.
    function sameOriginPath(input) {
        try {
            var raw = typeof input === 'string' ? input : (input && (input.url || String(input)));
            if (!raw) return null;
            var u = new URL(raw, window.location.href);
            if (u.origin !== window.location.origin) return null;
            return u.pathname;
        } catch (e) {
            return null;
        }
    }

    function isGuardedPath(path) {
        if (!path || path.indexOf('/api/') !== 0) return false;
        var p = path.length > 1 && path.charAt(path.length - 1) === '/' ? path.slice(0, -1) : path;
        for (var i = 0; i < EXCLUDED_PATHS.length; i++) {
            if (p === EXCLUDED_PATHS[i]) return false;
        }
        return true;
    }

    function signOut() {
        if (handled) return;
        handled = true;
        // Same effect as AuthService.clearCredentials() / the Logout buttons.
        // The theme is a device preference, not a credential: keep it across sign-out.
        var theme = null;
        try { theme = window.localStorage.getItem('nfl_wins_theme'); } catch (e) { /* ignore */ }
        try { window.localStorage.clear(); } catch (e) { /* ignore */ }
        if (theme) {
            try { window.localStorage.setItem('nfl_wins_theme', theme); } catch (e) { /* ignore */ }
        }

        var redirected = false;
        function go() {
            if (redirected) return;
            redirected = true;
            try { window.location.assign('/'); } catch (e) { /* ignore */ }
        }
        // The httpOnly session_token cookie can only be cleared by the server.
        // Best effort; never wait longer than 2s before redirecting.
        try {
            setTimeout(go, 2000);
            origFetch.call(window, '/api/logout', { method: 'POST' }).then(go, go);
        } catch (e) {
            go();
        }
    }

    function inspect(response) {
        try {
            if (!response || response.status !== 401 || handled) return;
            if (!hasSavedLogin()) return;

            var state = null;
            try {
                state = response.headers && response.headers.get('X-Session-State');
            } catch (e) { /* ignore */ }
            if (state && DEAD_STATES[String(state).toLowerCase()]) {
                signOut();
                return;
            }

            // Older responses without the header: match the detail text. Clone
            // synchronously so the caller's own body read is unaffected.
            var copy = response.clone();
            copy.json().then(function (body) {
                var detail = body && typeof body.detail === 'string' ? body.detail : '';
                if (detail && DEAD_DETAIL.test(detail)) signOut();
            }, function () { /* not JSON: leave the user alone */ });
        } catch (e) {
            /* the guard must never break a request */
        }
    }

    window.fetch = function (input) {
        var promise = origFetch.apply(window, arguments);
        try {
            if (isGuardedPath(sameOriginPath(input))) {
                promise.then(inspect, function () { /* caller handles the rejection */ });
            }
        } catch (e) { /* ignore */ }
        return promise;
    };
})();
