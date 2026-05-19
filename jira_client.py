"""
jira_client.py
Shared Jira HTTP helpers used by build_dashboard.py and v2_timeline.py.
Reads credentials from environment (loaded via dotenv in the calling script).
"""

import os
import requests

JIRA_BASE = "https://api.atlassian.com/ex/jira/85005dc7-cde3-4a2c-8e65-2d746db228ed/rest/api/3"
JSON_HEADERS = {"Accept": "application/json"}


def _auth():
    return (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])


def jira_get(path, params=None):
    r = requests.get(f"{JIRA_BASE}{path}", auth=_auth(),
                     headers=JSON_HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def jira_post(path, body):
    r = requests.post(f"{JIRA_BASE}{path}", auth=_auth(),
                      headers={**JSON_HEADERS, "Content-Type": "application/json"},
                      json=body)
    r.raise_for_status()
    return r.json()


def jira_jql(jql, fields, max_results=100):
    """Paginate all JQL results using POST /search/jql (GET /search is 410 Gone)."""
    issues, token = [], None
    while True:
        body = {"jql": jql, "fields": fields, "maxResults": max_results}
        if token:
            body["nextPageToken"] = token
        data = jira_post("/search/jql", body)
        issues.extend(data.get("issues", []))
        if data.get("isLast", True):
            break
        token = data.get("nextPageToken")
        if not token:
            break
    return issues
