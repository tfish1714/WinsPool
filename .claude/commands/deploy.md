Deploy WinsPool to Google Cloud Run. Run pre-flight checks before touching anything.

Steps:
1. **Git check** — run `git status` and `git log --oneline -3`. Warn if not on the `main` branch.
2. **Commit** — if there are uncommitted changes, run `git diff --stat` to summarize them, draft a concise commit message, and show it to the user for approval. Once approved, stage all changed tracked files with `git add -u` and commit. If the working tree is clean, skip this step.
3. **Tests** — run `pytest tests/ -q`. If any tests fail, stop and report which ones. Do not proceed.
4. **E2E Tests** — run `pytest tests_e2e/ -v` (requires `E2E_TEST_PLAYER_IDS` and `E2E_TEST_PLAYER_PASSWORD` in `.env` per Task 3). If any tests fail, stop and report which ones. Do not proceed. **Note:** `tests_e2e/test_live_draft.py` is the slowest test in the suite (a real 30-pick draft) and typically takes 2–3 minutes to complete — this is a deliberate tradeoff for pre-deploy confidence, not a bug.
5. **Push** — run `git push origin main`. If the push fails, stop and report the error.
6. **Confirm** — show the last commit message and ask for explicit confirmation before deploying. Wait for their go-ahead.
7. **Deploy** — run `.\deploy\deploy.ps1` using the PowerShell tool. Stream output so the user can see Cloud Build progress.
8. **Result** — report success or failure. On success, print the service URL: https://winspool-1045965963135.us-east1.run.app
9. **Archive docs** (on success only) — find open specs and plans and match them to the work being deployed:
   - List files in `docs/superpowers/specs/` and `docs/superpowers/plans/` (not in `completed/`)
   - Read recent commit messages (`git log --oneline -20`) and the names/slugs of the open docs
   - Match by topic: if a spec or plan's slug clearly matches what the commits describe, move it with `git mv` to the corresponding `completed/` subfolder. Commit any moves as `docs: archive completed spec(s)/plan(s)`.
   - If a file's topic is ambiguous or doesn't match the deployed work, leave it and mention it so the user can decide later.

If the deploy script is missing (deploy/ is gitignored), tell the user it needs to be present locally and stop.
