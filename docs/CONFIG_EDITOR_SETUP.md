# Config Editor Setup — One-time GitHub PAT

The "Save as default for everyone" button in the dashboard's ⚡ What-If panel commits a config file change to this repo and opens a PR. To do that, your browser needs a GitHub Personal Access Token (PAT) that has write access to `mayankyadav1994/pong-exec-dashboard`. This is a one-time setup, ~2 minutes.

You only need to do this if **you** are someone who's expected to make config changes that affect every dashboard viewer. Casual viewers don't need a PAT — they can still preview changes in their own browser via the panel, just without the "Save as default" step.

## Prerequisites

- A GitHub account with **write access** (Collaborator role or higher) to `mayankyadav1994/pong-exec-dashboard`. If you don't have that, ask the repo admin to add you first.

## Step 1 — Mint a fine-grained PAT

1. Sign in to GitHub and open **[Settings → Developer settings → Personal access tokens → Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)**.
2. **Token name**: `pong-exec-dashboard editor` (or anything memorable).
3. **Expiration**: 90 days is a sensible default — you'll re-mint when prompted.
4. **Resource owner**: `mayankyadav1994`.
5. **Repository access**: choose **Only select repositories** → `mayankyadav1994/pong-exec-dashboard`.
6. **Repository permissions** — grant these three, leave everything else as "No access":
   - **Contents**: `Read and write` (commit the config file)
   - **Pull requests**: `Read and write` (open & merge the PR)
   - **Metadata**: `Read-only` (auto-required)
7. Click **Generate token**.
8. **Copy the token immediately** — it's shown only once. Format looks like `github_pat_11A…` (~90 characters).

## Step 2 — Paste it into the dashboard

1. Open the dashboard ([iGaming](https://mayankyadav1994.github.io/pong-exec-dashboard/igaming-timeline.html) or [V2](https://mayankyadav1994.github.io/pong-exec-dashboard/v2-timeline.html)).
2. Click the **⚡ What-If** button (bottom-right).
3. Switch to the **📋 Fix Versions** or **⚙ Settings** tab.
4. Make at least one edit (color change, hide an FV, add a holiday — anything).
5. Click **💾 Save as default** in the panel footer.
6. The first time, you'll see a modal asking for your PAT. Paste the token and click **Verify & Save**.
7. Once verified, the save modal opens automatically. Review the diff, optionally edit the commit message, click **Save & merge**.

The token is stored in your browser's `localStorage` and sent only to `api.github.com`. It never leaves your device otherwise — there's no backend.

## What happens when you save

1. Browser → GitHub API: create a branch `config/igaming-<timestamp>` from `main`.
2. Browser → GitHub API: commit the updated `config/igaming.json` to that branch.
3. Browser → GitHub API: open a PR for the branch.
4. Browser → GitHub API: merge the PR via the API (auto-merge).
5. GitHub Actions: the `iGaming Timeline` workflow has a `push.paths` trigger on `config/igaming.json` — the merge fires it.
6. Workflow rebuilds `igaming-timeline.html` with the new config and commits it. GitHub Pages redeploys.
7. ~2-3 minutes after you click Save, refresh the dashboard and you'll see the change for everyone.

All steps are visible in the [repo's PR list](https://github.com/mayankyadav1994/pong-exec-dashboard/pulls?q=is%3Apr+label%3Aconfig) so there's an audit trail of who changed what and when.

## Rotating or revoking your token

- **Rotate** (recommended every 90 days): mint a new PAT (Step 1), then click **Save as default** again — the dashboard will prompt you to re-enter the token when the old one expires.
- **Revoke immediately**: [Settings → Developer settings → Personal access tokens → Fine-grained tokens](https://github.com/settings/personal-access-tokens) → find this token → **Revoke**. The dashboard's stored copy then stops working; on next save attempt it'll prompt for a new one.

## Troubleshooting

| Error in the dashboard | What it means | Fix |
|---|---|---|
| `<user> can't push to <repo> — ask an admin…` | Your PAT is valid but the user it belongs to isn't a write-access collaborator. | Repo admin needs to add you under [Settings → Collaborators](https://github.com/mayankyadav1994/pong-exec-dashboard/settings/access). |
| `HTTP 401: Bad credentials` | Token is expired or revoked. | Mint a new PAT (Step 1), re-paste it. |
| `HTTP 403: Resource not accessible by personal access token` | Fine-grained PAT is missing one of the required permissions (most often Pull requests). | Re-mint with all three permissions from Step 1. |
| `HTTP 422: Reference already exists` | Two saves landed within the same millisecond. Rare. | Click Save again — branch name includes a timestamp, so retry works. |
| Save succeeded but the dashboard hasn't updated after 5 min | Workflow ran but Pages might be slow, or the workflow failed. | Check [recent runs of the iGaming Timeline workflow](https://github.com/mayankyadav1994/pong-exec-dashboard/actions/workflows/igaming_timeline.yml). |

## Security notes

- The PAT lives only in your browser's `localStorage` for this specific origin. It does not sync across browsers or devices. Anyone with physical access to your unlocked browser could read it.
- The PAT is scoped to **one repository** with **only the three permissions** above — it cannot delete anything, change settings, or touch other repos.
- All requests go directly browser → `api.github.com`. There is no proxy or middleware that could see the token in transit.
- If you suspect compromise, revoke the token immediately (link in "Rotating" section above).

## Removing the editor capability from your browser

If you want the panel to forget your PAT (e.g., shared computer):

```js
// Open browser devtools (F12) → Console:
localStorage.removeItem('ig_github_pat');
localStorage.removeItem('ig_github_pat_user');
```

The "Save as default" button will then prompt for a new PAT next time it's clicked.
