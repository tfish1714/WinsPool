# Auth Hardening Follow-Ups: Profile Update Auth, MFA Attempt Limits, VAPID Secret

**Date:** 2026-09-26
**Status:** Design, not yet planned or implemented.
**Origin:** Findings from the whole-branch review of
`worktree-auth-security-and-push` (merged 2026-09-25, commit `14c873e`), which
added per-IP rate limiting to `/api/login`, `/api/set_password` and
`/api/check_player`. The review surfaced three gaps that were deliberately left
out of that branch because each is a behavior or infrastructure change, not a
mechanical guard. GitHub #43 (IP rate limiting) is the parent issue.

## Context: what exists today

- `services/rate_limit_service.py` provides an in-process sliding-window
  `RateLimiter` (no lock; only safe from `async def` handlers) and `client_ip()`
  (right-most `X-Forwarded-For` entry, `TRUSTED_PROXY_HOPS` default 1).
  `routes/auth_routes.py` holds `_login_limiter` (5/min/IP, env
  `AUTH_RATE_LIMIT_PER_MINUTE`) and `_lookup_limiter` (30/min/IP), and
  `_rate_limited_response(request, limiter)` returns the 429 + `Retry-After`.
- Production is pinned to one Cloud Run instance (`--max-instances=1` in
  `deploy/deploy.ps1`), which is why in-process limiter state is correct.
- Sessions: `login` and `set_password` and `mfa/verify` mint a JWT
  (`create_token(player_id, role)`) and set an httpOnly `session_token` cookie.
  `require_auth` (services/session_service.py) accepts either a Bearer header or
  that cookie and returns the decoded payload (`sub` is the player id).
- Per-account lockout exists for login (`failed_login_attempts`,
  `lockout_until`, 5 failures then 30 minutes) and set_password
  (`failed_setup_attempts`).

## 1. `/api/profile/update` is unauthenticated (highest priority)

### Problem

`POST /api/profile/update` (`routes/auth_routes.py`, `update_profile`) has no
`require_auth`. It trusts `playerId` from the request body and verifies only
`currentPassword`. Consequences:

- It is an unlimited-per-account password-guessing oracle: a wrong password
  returns 401 "Incorrect current password" and is not counted against
  `failed_login_attempts` or `lockout_until`, so it bypasses the per-account
  lockout entirely.
- The per-IP limit added in the merged branch caps this at 5 attempts per minute
  per IP, which slows it but does not close it (a distributed guesser is not
  bounded; player ids are small integers).
- A correct guess allows changing email, password and `mfa_enabled` (including
  turning MFA off), which is full account takeover.

The only caller is `templates/profile.html` (~line 173), a same-origin `fetch`
with no explicit `Authorization` header; it relies on the httpOnly
`session_token` cookie, which `require_auth` already accepts.

### Requirements

1. Add `_auth: dict = Depends(require_auth)` to `update_profile`. Reject with
   403 `{"error": "Forbidden."}` when `int(_auth["sub"]) != int(body.playerId)`.
   Keep the request body shape unchanged so `profile.html` needs no functional
   change.
2. Keep the existing `currentPassword` re-verification (re-auth for sensitive
   changes is correct and stays), but count failures: a wrong `currentPassword`
   increments `failed_login_attempts` and applies the same 5-failure / 30-minute
   `lockout_until` rule as `/api/login`, and a locked account returns the same
   429 message shape. Successful verification resets the counter only if the
   login flow does (mirror `login`, do not invent new semantics).
3. Keep the rate-limit guard added in the merged branch as the first statement
   (defense in depth, unauthenticated floods are still cheap to reject).
4. No change to the response shapes on success (`{"message": ...}`), so the
   existing front end keeps working.

### Approach notes

- Compare ids as strings after normalising both (`str(_auth["sub"])`,
  `str(body.playerId)`), since `playerId` is a string in some models and an
  int in the JWT.
- Extract the lockout bookkeeping in `login` into a small shared helper only if
  it can be done without changing `login`'s behavior; otherwise duplicate the
  five lines. Do not refactor `login` in this change.
- An admin editing another player's profile is a separate flow
  (`/api/admin/update_player`); do not widen this endpoint for admins.

### Tests

- 401 without a token; 403 when the token's `sub` differs from `playerId`; 200
  with a matching token and correct password; 401 with a wrong password.
- Five wrong passwords lock the account; the sixth returns 429 even with the
  correct password; the lock also blocks `/api/login` (same `lockout_until`).
- Cookie-only auth (no Authorization header) works, since that is what
  `profile.html` sends.
- E2E: `tests_e2e/test_profile.py` still passes unchanged.

### Out of scope

Changing the password-change UX, requiring MFA for profile changes, or moving
to per-request CSRF tokens (SameSite=lax already applies to the cookie).

## 2. `/api/mfa/verify` has no attempt limit

### Problem

`POST /api/mfa/verify` checks a 6-digit code against `mfa_token` (a SHA-256 of
the code) with a 600-second expiry (`routes/auth_routes.py` login flow sets
`mfa_expiry = time.time() + 600`). It has no rate limit and no counter, and a
wrong guess does not invalidate the code. Within one 10-minute window a single
client can attempt on the order of 10^4 to 10^5 guesses against a 10^6 code
space, so an attacker who already has the password can plausibly get in. This
matters less than section 1 (it requires the password first) but is the only
second-factor control and is currently unmetered. Note also that the endpoint
takes `playerId` in the body and is unauthenticated by design (the caller has
no session yet).

### Requirements

1. Per-code attempt cap: store `mfa_attempts` on the player document, reset to 0
   when a new code is issued. After 5 wrong attempts, invalidate the code
   (`mfa_token: None`, `mfa_expiry: 0`) and return 401 with a message telling
   the user to log in again to get a new code. A correct code succeeds only if
   attempts are below the cap.
2. Per-IP limit: add `/api/mfa/verify` to the existing login bucket
   (`_login_limiter`) as the first statement, using `_rate_limited_response`.
   Handler stays `async def`.
3. Comparison must use `hmac.compare_digest` on the hex digests (constant-time),
   replacing the plain `!=`.
4. Issuing a code (login with `mfa_enabled`) must reset `mfa_attempts`.
5. Log at WARNING when a code is invalidated by the attempt cap (player id only;
   never log the submitted code).

### Approach notes

- The attempt counter lives on the Firestore player document like the other
  lockout fields; use the existing `update_player_profile`. Treat a NaN or
  missing `mfa_attempts` as 0 via the existing `_int_field` helper (same
  pandas-NaN pitfall documented in that helper).
- The code is 6 digits generated with `secrets.randbelow`; do not change its
  length in this change (UX and email templates depend on it).
- Do not add a lockout of the whole account on MFA failures; invalidating the
  code is enough because the attacker must pass the password step again to get
  a new one, and that step is itself limited.

### Tests

- Four wrong codes leave the code valid; the fifth wrong code invalidates it and
  a subsequent correct code also fails (401) until a new code is issued.
- A correct code with attempts below the cap succeeds and clears the code.
- Issuing a new code resets the attempt counter.
- Per-IP: the sixth verify request in a minute from one IP returns 429 with
  `Retry-After`, sharing the login bucket.
- NaN / missing `mfa_attempts` is treated as 0.
- Existing `tests/test_mfa_hashing.py` and `tests_e2e/test_mfa.py` pass
  unchanged.

### Out of scope

Switching to TOTP or WebAuthn, changing the code length or expiry, or adding
per-account MFA lockouts.

## 3. VAPID private key is a plaintext environment variable

### Problem

`deploy/deploy.ps1` reads `VAPID_PRIVATE_KEY` from `.env` and passes it in
`--set-env-vars`, so it is stored as a plaintext env var on the live Cloud Run
service and readable by anyone with `run.services.get`. The other five secrets
(`FIREBASE_CREDENTIALS`, `GEMINI_API_KEY`, `SMTP_PASSWORD`, `JWT_SECRET`,
`RESEND_API_KEY`) are mounted from Secret Manager via `--set-secrets`. The
VAPID private key signs Web Push requests; exposure lets a holder send pushes as
the app to subscribed browsers.

### Requirements

1. Create a Secret Manager secret `VAPID_PRIVATE_KEY` (one-time, done by the
   operator, not by the deploy script) and grant the Cloud Run runtime service
   account `roles/secretmanager.secretAccessor` on it, the same way the other
   secrets are granted.
2. Change `deploy/deploy.ps1`: stop appending `VAPID_PRIVATE_KEY=...` to
   `$envVars`; add `VAPID_PRIVATE_KEY=VAPID_PRIVATE_KEY:latest` to the
   `--set-secrets` list. Keep `VAPID_PUBLIC_KEY` and `VAPID_CLAIMS_EMAIL` as env
   vars (the public key is rendered into a page meta tag; it is not secret).
3. The script's existing warning ("VAPID keys not both set") must keep working:
   it should now check that the secret exists (`gcloud secrets describe`) or, at
   minimum, no longer depend on the value being in `.env`. Do not print the key.
4. Remove the plaintext value from the live service on the first deploy after
   the change. Because `gcloud run deploy --set-env-vars` replaces the whole env
   var set and `--set-secrets` overrides a same-named env var only if it is
   removed first, the deploy must also pass `--remove-env-vars VAPID_PRIVATE_KEY`
   once. Document this one-time step in DEPLOY.md; the steady-state script does
   not need the flag after the first successful deploy (a test should guard
   that the key never returns to `$envVars`).
5. Rotate the key after cutover: the old value was exposed as plaintext. Rotating
   invalidates existing push subscriptions (subscribers were created against the
   old public key), so rotation forces every subscribed browser to resubscribe.
   Decide deliberately: rotate now and accept resubscription, or accept the
   residual exposure risk (project-viewer access only) and defer. Record the
   decision in DEPLOY.md.

### Approach notes

- `services/push_service.py` reads `VAPID_PRIVATE_KEY` from `os.environ` at
  import, which works unchanged with a mounted secret exposed as an env var. No
  application code change is required.
- The `tests/test_deploy_config.py` guard added in the merged branch can be
  extended: assert `VAPID_PRIVATE_KEY` appears in the `--set-secrets` string and
  not in the `$envVars` additions.
- This change is infrastructure: verify with read-only
  `gcloud run services describe winspool --region us-east1 --project
  fishbone-wins-pool` after deploy (the var should show as a secret reference,
  not a literal). Do not run `deploy.ps1` from an automated session; the
  operator deploys.

### Tests

- `tests/test_deploy_config.py`: `VAPID_PRIVATE_KEY` in `--set-secrets`, absent
  from `$envVars` construction; other flags preserved.
- Manual: post-deploy, a test push (admin broadcast from the merged branch) is
  delivered, proving the mounted secret is read correctly.

### Out of scope

Rotating `JWT_SECRET` or other secrets, moving public config to Secret Manager,
or a general secrets audit (worth its own pass; the same review noted only this
one plaintext credential).

## Sequencing and dependencies

Recommended order, each independently shippable:

1. Section 1 (profile update auth): closes the account-takeover path and the
   lockout bypass; smallest blast radius on the front end.
2. Section 2 (MFA attempts): independent of section 1; can be built in parallel
   in a separate worktree but both touch `routes/auth_routes.py` and
   `tests/test_auth.py`, so merge sequentially to avoid conflicts.
3. Section 3 (VAPID secret): pure infrastructure, no dependency on the others.
   The operator performs the Secret Manager and IAM steps; the code change is a
   few lines of `deploy.ps1` plus a test.

## Review focus (inputs the design implies but the sections above do not pin)

- A player whose JWT `sub` is an int but whose Firestore `playerId` is a string
  or float: the ownership comparison in section 1 must normalise both sides.
- `profile.html` sending a stale or missing cookie: it should get a clean 401
  and the existing error alert, not a 500.
- Section 2 attempt counter racing with a concurrent verify: with one instance
  and `async def` handlers the check-then-update is serialized on the event
  loop, but the Firestore write is not transactional; a lost update can allow
  at most one extra guess and is acceptable.
- A user who requests a new MFA code (logs in again) while an old code is
  pending: the new code replaces the old and resets attempts (section 2, req 4).
- Section 3 first deploy: forgetting `--remove-env-vars` leaves the plaintext
  var in place and the secret mount silently ineffective; the runbook step and
  the post-deploy `describe` check exist to catch this.
