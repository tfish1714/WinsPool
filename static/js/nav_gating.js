/**
 * nav_gating.js -- seasonal visibility rules for nav links.
 *
 * The Playoff Race link clutters the early season, so it only appears once
 * the active season reaches PLAYOFF_RACE_MIN_WEEK. Must stay in sync with
 * services/constants.py::PLAYOFF_RACE_MIN_WEEK (tests/test_nav_gating_js.py
 * asserts the two match). Anything that is not a finite number (missing,
 * null, a stale cache value) hides the link: fail closed.
 */
export const PLAYOFF_RACE_MIN_WEEK = 10;

export function isPlayoffRaceVisible(latestWeek) {
    if (latestWeek === null || latestWeek === undefined || latestWeek === '') return false;
    const week = Number(latestWeek);
    return Number.isFinite(week) && week >= PLAYOFF_RACE_MIN_WEEK;
}
