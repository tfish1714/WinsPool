/**
 * tiebreaker_explain.js -- explains WHY tied players are ranked where they
 * are. Standings sort by TotalWins, then six tiers in order (see
 * services/constants.py::TIEBREAKER_SORT_COLS, all descending). For each
 * adjacent pair with equal totals, the first tier whose values differ is the
 * decisive one: that cell is highlighted and explained in a tooltip.
 *
 * The DOM layer reads the rendered data-role cells (not server JSON) so it
 * stays correct after standings_refresh.js patches values in place; it
 * re-runs on the `standings:patched` event.
 */
export const TIERS = [
    { key: 'tb1', label: 'TB1', description: 'worst team wins' },
    { key: 'tb2', label: 'TB2', description: '2nd-worst team wins' },
    { key: 'tb3', label: 'TB3', description: 'best team wins' },
    { key: 'tb4', label: 'TB4', description: 'worst team point differential' },
    { key: 'tb5', label: 'TB5', description: '2nd-worst team point differential' },
    { key: 'tb6', label: 'TB6', description: 'best team point differential' },
];

export function parseCell(text) {
    if (text === null || text === undefined) return 0;
    const n = parseInt(String(text).replace('+', '').trim(), 10);
    return Number.isFinite(n) ? n : 0;
}

export function decisiveTier(prev, cur) {
    if (!prev || !cur || prev.total !== cur.total) return null;
    for (let i = 0; i < TIERS.length; i++) {
        if (prev.tb[i] !== cur.tb[i]) return { tier: i + 1, key: TIERS[i].key };
    }
    return null;
}

export function explainTie(prev, cur, prevName, curName) {
    const hit = decisiveTier(prev, cur);
    if (!hit) {
        return `${prevName} and ${curName} are level on wins and on all six tiebreakers; their order is not decided by the cascade.`;
    }
    const tier = TIERS[hit.tier - 1];
    return `${prevName} and ${curName} are tied on ${prev.total} wins. ` +
        `${prevName} ranks ahead on ${tier.label} (${tier.description}): ` +
        `${prev.tb[hit.tier - 1]} vs ${cur.tb[hit.tier - 1]}. ` +
        `Cascade: total wins, then worst team wins, 2nd-worst, best team wins, ` +
        `then worst, 2nd-worst and best team point differential.`;
}

/**
 * Pure: entries are `{id, name, total, tb[6]}` in rank order. Returns one
 * `{id, key, text}` per (player, decisive tier, adjacent pair). A player in
 * the middle of a 3+ way tie is in two pairs, so it appears twice, on
 * different tiers or on the same tier.
 */
export function computeHighlights(entries) {
    const out = [];
    for (let i = 1; i < entries.length; i++) {
        const prev = entries[i - 1];
        const cur = entries[i];
        const hit = decisiveTier(prev, cur);
        if (!hit) continue;
        const text = explainTie(prev, cur, prev.name, cur.name);
        out.push({ id: prev.id, key: hit.key, text });
        out.push({ id: cur.id, key: hit.key, text });
    }
    return out;
}

/** Pure: {playerId: {tierKey: [explanations...]}} so no explanation is clobbered. */
export function groupHighlights(entries) {
    const grouped = {};
    computeHighlights(entries).forEach(({ id, key, text }) => {
        grouped[id] = grouped[id] || {};
        (grouped[id][key] = grouped[id][key] || []).push(text);
    });
    return grouped;
}

function readEntry(card) {
    const val = (role) => parseCell(card.querySelector(`[data-role="${role}"]`)?.textContent);
    return {
        id: card.dataset.playerId,
        name: card.querySelector('.standings-stacked-card__name')?.textContent?.trim() || 'Player',
        total: val('total'),
        tb: TIERS.map((t) => val(t.key)),
    };
}

// The highlighted element is the [data-role="tbN"] span itself: it is the
// visible number in the desktop row (inside .tb-v) and in the stacked card.
// standings_refresh.js patches its text in place, so attributes survive a
// patch and are recomputed on `standings:patched`.
export function applyTiebreakerHighlights(root = document) {
    root.querySelectorAll('.tb-decisive').forEach((el) => {
        el.classList.remove('tb-decisive');
        el.removeAttribute('data-tb-explain');
        el.removeAttribute('tabindex');
    });
    // The mobile stacked cards list every player (including rank 1) with all
    // six tiers, so they are the canonical data source on every viewport.
    const entries = Array.from(root.querySelectorAll('.standings-stacked-card')).map(readEntry);
    const grouped = groupHighlights(entries);
    Object.keys(grouped).forEach((id) => {
        Object.keys(grouped[id]).forEach((key) => {
            const text = grouped[id][key].join('\n\n');
            root.querySelectorAll(`[data-player-id="${id}"] [data-role="${key}"]`).forEach((el) => {
                el.classList.add('tb-decisive');
                el.setAttribute('data-tb-explain', text);
                el.setAttribute('tabindex', '0');
            });
        });
    });
}

function initTooltip() {
    const tip = document.getElementById('tb-tooltip');
    if (!tip) return;
    const show = (el) => {
        tip.textContent = el.getAttribute('data-tb-explain') || '';
        tip.hidden = false;
        const r = el.getBoundingClientRect();
        tip.style.top = `${window.scrollY + r.bottom + 8}px`;
        tip.style.left = `${Math.max(8, Math.min(window.scrollX + r.left, window.innerWidth - tip.offsetWidth - 8))}px`;
    };
    const hide = () => { tip.hidden = true; };
    document.addEventListener('mouseover', (e) => {
        const el = e.target.closest?.('.tb-decisive');
        if (el) show(el);
    });
    document.addEventListener('mouseout', (e) => {
        if (e.target.closest?.('.tb-decisive')) hide();
    });
    document.addEventListener('focusin', (e) => {
        const el = e.target.closest?.('.tb-decisive');
        if (el) show(el);
    });
    document.addEventListener('focusout', hide);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
    // Touch: a tap fires click; a tap outside a decisive cell dismisses.
    document.addEventListener('click', (e) => {
        const el = e.target.closest?.('.tb-decisive');
        if (el) show(el); else hide();
    });
    // Visible text may be stale after a live patch; the next hover/focus re-reads it.
    document.addEventListener('standings:patched', hide);
}

function init() {
    applyTiebreakerHighlights();
    initTooltip();
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    document.addEventListener('standings:patched', () => applyTiebreakerHighlights());
}
