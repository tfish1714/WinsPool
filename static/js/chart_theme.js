// Theme-aware colors for Chart.js. Charts draw to a canvas, which cannot use CSS
// variables, so colors are read from the active theme's tokens when a chart is built
// and repainted when theme_init.js announces a change ('wins-theme-change').
(function () {
    // Dark-theme values, used only if a token cannot be read.
    var FALLBACK = {
        text: 'rgba(255,255,255,0.7)', muted: 'rgba(255,255,255,0.5)', grid: 'rgba(255,255,255,0.1)',
        tipBg: 'rgba(26,30,38,0.95)', tipTitle: '#e8eaef', tipBody: '#9aa1ad', tipBorder: 'rgba(255,255,255,0.14)',
    };

    function token(name, fallback) {
        try {
            var v = window.getComputedStyle(document.documentElement).getPropertyValue(name);
            v = v ? v.trim() : '';
            return v || fallback;
        } catch (e) { return fallback; }
    }

    function colors() {
        return {
            text: token('--ink-2', FALLBACK.text),
            muted: token('--ink-3', FALLBACK.muted),
            grid: token('--line', FALLBACK.grid),
            tipBg: token('--bg-elev-2', FALLBACK.tipBg),
            tipTitle: token('--ink', FALLBACK.tipTitle),
            tipBody: token('--ink-2', FALLBACK.tipBody),
            tipBorder: token('--line-strong', FALLBACK.tipBorder),
        };
    }

    // Recolors an existing chart's options in place (call chart.update() afterwards).
    function paint(chart) {
        var c = colors();
        var o = chart.options || {};
        var plugins = o.plugins || {};
        if (plugins.tooltip) {
            plugins.tooltip.backgroundColor = c.tipBg;
            plugins.tooltip.titleColor = c.tipTitle;
            plugins.tooltip.bodyColor = c.tipBody;
            plugins.tooltip.borderColor = c.tipBorder;
        }
        if (plugins.title) plugins.title.color = c.text;
        var scales = o.scales || {};
        Object.keys(scales).forEach(function (k) {
            var s = scales[k];
            s.ticks = s.ticks || {};
            s.ticks.color = c.text;
            s.grid = s.grid || {};
            s.grid.color = c.grid;
            if (s.title) s.title.color = c.muted;
        });
    }

    // Repaints the chart whenever the theme changes; stops once the chart is destroyed.
    function track(chart) {
        function onChange() {
            if (!chart.canvas) {
                window.removeEventListener('wins-theme-change', onChange);
                return;
            }
            paint(chart);
            chart.update('none');
        }
        window.addEventListener('wins-theme-change', onChange);
    }

    // Every live Chart.js instance repaints on a theme change, so pages need no per-chart wiring.
    window.addEventListener('wins-theme-change', function () {
        var instances = (window.Chart && window.Chart.instances) || {};
        Object.keys(instances).forEach(function (k) {
            var chart = instances[k];
            if (chart && chart.canvas) {
                paint(chart);
                chart.update('none');
            }
        });
    });

    window.WinsPoolChartTheme = { colors: colors, paint: paint, track: track };
})();
