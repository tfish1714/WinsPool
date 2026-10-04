// Applies the saved (or system) theme before first paint and exposes a toggle.
// Classic script loaded from <head>: a deferred module such as main.js would run after
// the first paint and flash the dark theme. Every storage access is guarded.
(function () {
    var KEY = 'nfl_wins_theme';

    function stored() {
        try {
            var v = window.localStorage.getItem(KEY);
            return v === 'light' || v === 'dark' ? v : null;
        } catch (e) { return null; }
    }

    function system() {
        try {
            return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        } catch (e) { return 'dark'; }
    }

    function apply(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        // Canvas charts cannot use CSS variables; they repaint on this event (chart_theme.js).
        try { window.dispatchEvent(new CustomEvent('wins-theme-change', { detail: { theme: theme } })); } catch (e) { /* no DOM events */ }
        return theme;
    }

    function current() {
        return document.documentElement.getAttribute('data-theme') || stored() || system();
    }

    function set(theme) {
        try { window.localStorage.setItem(KEY, theme); } catch (e) { /* storage unavailable */ }
        return apply(theme);
    }

    window.WinsPoolTheme = {
        KEY: KEY,
        get: current,
        set: set,
        toggle: function () { return set(current() === 'light' ? 'dark' : 'light'); },
        init: function () { return apply(stored() || system()); },
    };
    window.WinsPoolTheme.init();
})();
