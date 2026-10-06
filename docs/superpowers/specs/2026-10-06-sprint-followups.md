# Follow-Ups From the Recap / iOS Push / Exception Handler / Nav Sprint

**Date:** 2026-10-06
**Status:** Backlog. Open items left after the sprint in
`docs/superpowers/plans/completed/2026-10-05-admin-recap-ios-push-exception-nav.md`
(branch `sprint/recap-ios-exceptions-nav`, issues #82, #67, #104). Each item needs
a short design pass before work starts unless it says otherwise.

## What shipped (for context)

- Admin AI Recaps tab auto-populates year/week and auto-previews the prompt
  (`POST /api/admin/recap/preview_prompt` now takes optional `year`/`week` and
  echoes them), plus a Copy prompt button. Generation and broadcast remain manual.
- `static/manifest.json`, Apple meta tags, and a dismissible iOS "Add to Home
  Screen" banner (`static/js/ios_push_hint.js`) so push can work on iOS.
- Global `Exception` handler in `main.py`; generic try/except -> `server_error()`
  wrappers removed from admin/api/prediction/standings routes.
- `updateNav()` split into five helpers; header brand links to `/wins-pool`.

## Open items

### 1. Proper PWA icons (needs assets)

The manifest and `apple-touch-icon` reference `/static/fishbone.png` with
`sizes: "any"`. iOS and Android install quality is better with dedicated 180x180
(apple-touch-icon), 192x192 and 512x512 PNGs, and a maskable variant.
**Needs:** the owner supplies or approves artwork; then add the files under
`static/`, list them in `manifest.json`, point `apple-touch-icon` at the 180px
file, and extend `tests/test_templates.py` (it already checks every icon file
exists).

### 2. Browser and e2e verification never run

The sprint was verified by unit and node tests only (2386 passed at the final
whole-branch review). Not run: `tests_e2e/test_admin_members_and_recap.py`
(including the new Copy button check), `tests_e2e/test_nav_parity.py`, and any
visual check at desktop and ~390px of: the iOS banner, the Copy button, the
refactored nav, and the header brand link. **Action:** seed e2e env vars
(`scripts/seed_e2e_test_players.py`), run both e2e files, and do one manual pass
at mobile width. In particular confirm the 72px `--bottom-tab-bar-height` guess
matches the real tab bar, so the banner clears it.

### 3. Copy button e2e covers only the weekly preview

`tests_e2e/test_admin_members_and_recap.py` asserts Copy after the weekly
preview. The draft-recap preview path (fixed in `635261d`) has no browser test.
Add one after item 2 is runnable.

### 4. iOS Chrome / Firefox hint accuracy

`ios_push_hint.js` treats any iOS user agent (including CriOS/FxiOS) as needing
"Add to Home Screen". The Share-sheet wording may not match those browsers, and
push support differs by iOS version. **Decide:** keep the generic text, or detect
CriOS/FxiOS and show browser-specific copy. Verify on a real device first.

### 5. Global 500 responses and CORS

The global handler runs in Starlette's `ServerErrorMiddleware`, outside the CORS
and GZip middleware, so unhandled-error 500s probably lack CORS headers (the old
in-route `server_error()` responses went through them). Moot while the app is
same-origin. **If** a cross-origin client is ever added, wrap the handler's
response with the CORS headers or move error shaping into middleware. Confirm
with a test at that time.

### 6. Recap email HTML indentation

Dedenting `save_and_broadcast_recap` removed 4 leading spaces from every line of
the emailed `html_body` f-string. It renders identically, but the stored/sent
source differs. Optional: restore the original indentation, or normalize the
template into `services/email_service.py`.

### 7. updateNav helper boundaries

`_updatePrimaryLinks` also returns `showPlayoffRace` (not a pure updater), and the
`btb-playoff-tab` toggle lives in `_updateDrawer`, not `_updateBottomTabs`. Both
preserve original behavior. Optional cleanup: compute `showPlayoffRace` once in
`updateNav()` and pass it to the helpers that need it; move the bottom-tab toggle
into `_updateBottomTabs`. Requires the nav parity e2e (item 2) as a safety net.

### 8. Local-only docs

`docs/frontend.md` and `docs/architecture.md` are untracked (`docs/` is
gitignored) and were edited locally during the sprint (manifest/banner, recap
auto-preview, global 500 contract, nav helpers). Those edits are not in git.
`CLAUDE.md` and `docs/api_endpoints.md` carry the committed documentation.
**Decide:** track those two files, or re-home their content into tracked docs.

### 9. Test-only clients and the handler (hygiene)

Any new test asserting a 500 from an unhandled exception must use
`TestClient(app, raise_server_exceptions=False)`. Consider a shared fixture in
`tests/conftest.py` (e.g. `client_no_raise`) so new tests do not rediscover this.
Existing tests already use local non-raising clients.
