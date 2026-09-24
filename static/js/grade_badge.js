/**
 * grade_badge.js -- Badge for a graded bet in the "Why TEAM?" modal.
 *
 * The ATS grade from /api/predictions/explain is tri-state plus push:
 *   true   -> covered            (green check)
 *   false  -> did not cover      (red cross)
 *   "push" -> landed on the line (neutral PUSH label)
 *   null / anything else -> not played or not gradable (no badge)
 *
 * Strict comparisons on purpose: "push" is a non-empty string and would read
 * as a win under a plain truthiness check.
 */
export function gradeBadge(value) {
    if (value === true) {
        return '<span style="color:var(--accent-green); font-weight:700;">✓</span>';
    }
    if (value === false) {
        return '<span style="color:var(--accent-red); font-weight:700;">✗</span>';
    }
    if (value === 'push') {
        return '<span style="color:var(--text-secondary); font-weight:700; font-size:0.68rem; letter-spacing:0.05em;">PUSH</span>';
    }
    return '';
}
