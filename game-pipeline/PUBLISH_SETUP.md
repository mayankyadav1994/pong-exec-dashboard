# Publishing shared defaults — editor setup

The Game Pipeline dashboard is a static site, so "Save as default for everyone"
commits the shared plan to the repo via the **GitHub REST API** from your
browser. To publish you need (a) **write access** to `pong-exec-dashboard`, and
(b) your GitHub username listed in **`editors.json`**.

> The dashboard is public to view. Only publishing (permanent changes) is gated.
> Everyone else's tweaks stay local to their browser.

## One-time: create a fine-grained token
1. GitHub → Settings → Developer settings → **Fine-grained personal access tokens** → *Generate new token*.
2. **Repository access:** Only select repositories → `mayankyadav1994/pong-exec-dashboard`.
3. **Permissions → Repository → Contents: Read and write.**
4. **Permissions → Account → Email addresses: Read-only** (so the dashboard can
   match your GitHub account to your editor email; optional — GitHub login also
   matches as a fallback).
5. **Expiration:** short (e.g. 30–90 days).
6. Generate and copy the `github_pat_…` value.

## Add yourself as an editor
The allowlist is **by email** (matched to your GitHub account's verified emails).
Edit `editors.json` (root of the repo):
```json
{ "editors": ["mayank.yadav@pongstudios.com", "editor2@pongstudios.com", "editor3@pongstudios.com"] }
```
(This list is only a UX gate — the real check is repo write access. You must be a
repo collaborator with write permission for the commit to succeed. If your token
can't read your email, your GitHub username also matches as a fallback.)

## Publishing
1. Open the dashboard → **✎ Plan Mode** → make your edits (status, sizes, order, hidden, velocities).
2. Drawer footer → **🔐 Sign in to publish** → paste the token (kept in this tab's
   memory only; cleared on **sign out** or tab close).
3. **💾 Save as default for everyone** → **Commit to main**.
4. The shared plan redeploys in ~1–3 minutes and becomes the baseline for all
   viewers (shown with a 📌 badge). It overrides the Jira auto-pull until changed.

## Notes
- **Token safety:** fine-grained, single-repo, Contents-only, short expiry; never
  committed; only in `sessionStorage`.
- **Reverting:** *Reset local edits* clears only your browser. To change the shared
  baseline, an editor re-publishes; to drop a per-game pin, set it back to the
  auto value (↺ revert) and publish.
- **Per-project:** editors are global (can publish both V2 and iGaming).
