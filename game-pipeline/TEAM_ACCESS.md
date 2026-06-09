# Team access — who can change the Game Pipeline dashboard

The dashboard is **read-only for everyone** (stakeholders just open the URL).
Only people holding the **team token** can press *Save as default for everyone*,
which commits the current Plan to `plan-v2.json` / `plan-ig.json` and makes it the
shared baseline that survives every daily Jira rebuild (Decision #39 / #46).

The token is entered **once per browser** (stored in that browser's
`localStorage`), so a teammate pastes it a single time and never again on that
machine.

---

## 1. Mint the team token (admin / repo owner does this once)

GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**:

- **Token name:** `game-pipeline-editor`
- **Expiration:** pick a policy you'll re-issue on (e.g. 90 days). Shorter = safer.
- **Resource owner:** your account (`mayankyadav1994`).
- **Repository access:** *Only select repositories* → **`pong-exec-dashboard`**.
- **Permissions → Repository permissions → Contents:** **Read and write**.
  Leave everything else at *No access*.
- Generate → copy the `github_pat_…` string.

That token can do **nothing** except read/write files in this one repo — no other
repos, no settings, no secrets.

## 2. Give it to your team (not to stakeholders)

Share the token with editors only, over a secure channel (1Password / Bitwarden /
a DM — **not** email or a public channel). Each editor:

1. Opens the dashboard → **✎ Plan Mode** → **🔐 Sign in to publish**.
2. Pastes the token → **Verify & sign in**. Done — they won't be asked again on
   that browser.

Stakeholders simply use the URL and never receive the token, so they can view but
not change anything.

### Two ways to run it
- **One shared team token (simplest, matches "enter once and distribute").** All
  commits show the token owner's name. Revoke = regenerate the token (everyone
  re-pastes the new one).
- **One token per editor (better audit/▶revocation).** Each editor who is a repo
  collaborator mints their own fine-grained token the same way; commits are
  attributed to them and you can revoke one person without affecting others.

## 3. Revoke / rotate

GitHub → the same fine-grained tokens page → **Delete** (or let it expire). Any
browser still holding it will get `401 Bad credentials` on the next publish and
must paste a fresh token. Use **sign out** in the drawer footer to clear a token
from a browser.

---

## Notes & limits

- **Where it lives:** the token sits in the browser's `localStorage` for that
  origin. Treat it like a password; don't paste it on a shared/public computer
  (use *sign out* if you do).
- **Scope reality:** the token can write **any** file in `pong-exec-dashboard`,
  not only the plan JSONs — GitHub permissions don't go to single-file level. The
  app only ever writes `plan-*.json`, but the credential itself is repo-wide.
- **The real gate is GitHub**, not the app: a token whose account lacks write
  access to the repo is rejected by GitHub regardless of the `editors.json` list
  (which only hides the button as a convenience).
