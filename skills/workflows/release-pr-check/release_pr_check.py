#!/usr/bin/env python3
"""Release PR Check — 驗證 Regular Release 頁面各 section tickets 是否都已 merge。

Auth resolution order:
  1. ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN env vars
  2. kkday-qa-tools get_secret (if importable)
"""
import argparse, json, sys, re, html, os, requests
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def load_dotenv():
    """Load KEY=VALUE pairs from .env (next to script) into os.environ
    without overwriting existing vars. Quiet no-op if file missing."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_dotenv()

CLOUD_ID = "8b890302-cc52-42ce-a15e-697446426613"
JIRA_DOMAIN = "kkday.atlassian.net"
SKIP_STATUSES = {"Closed", "Won't Fix", "Duplicate", "Cancelled"}

# Issue key prefixes (project keys) to skip — non-RD coordination tickets that
# legitimately have no PRs. Add new entries when a project is confirmed PM-only.
SKIP_PROJECTS = {
    "B2CPM",   # B2C PM coordination tickets — not RD implementation
}

# Issue types to skip — parent / planning tickets that don't directly map to PRs.
# Sub-tasks under these are where the actual RD work (and PRs) live.
SKIP_ISSUETYPES = {
    "Epic",    # Epics are parent tickets; implementation lives in sub-tasks
}


def get_auth():
    """Return (email, api_token).
    Order: 1) env vars (incl. .env) 2) kkday-qa-tools get_secret fallback.
    """
    email = os.environ.get("ATLASSIAN_EMAIL")
    token = os.environ.get("ATLASSIAN_API_TOKEN")
    if email and token:
        return (email, token)
    try:
        from library.qa_service.get_secret import get_secret
    except ImportError:
        sys.exit(
            "ERROR: No Atlassian credentials found.\n"
            "  Option A: cp .env.example .env (next to release_pr_check.py) and fill in values\n"
            "  Option B: export ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN in your shell\n"
            "  Option C: add kkday-qa-tools to PYTHONPATH for get_secret fallback\n"
            "  Get an API token at https://id.atlassian.com/manage-profile/security/api-tokens"
        )
    raw = get_secret("production", "atlassian", "release_atlassian")
    if not raw:
        sys.exit("ERROR: get_secret returned None (VPN/network issue?)")
    secret = json.loads(raw[0]["value"])
    return (secret["email"], secret["api_token"])


def get_page_id(url):
    parts = urlparse(url).path.split("/")
    return parts[parts.index("pages") + 1]


def fetch_storage(page_id, auth):
    r = requests.get(
        f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}/wiki/api/v2/pages/{page_id}",
        params={"body-format": "storage"},
        auth=auth, headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    return r.json()["body"]["storage"]["value"]


def extract_datasource_jqls(storage):
    headings = [(m.start(), re.sub(r'<[^>]+>', '', m.group(1)).strip())
                for m in re.finditer(r'<h[23][^>]*>(.+?)</h[23]>', storage, re.DOTALL)]

    def nearest_heading(pos):
        prev = [h for p, h in headings if p < pos]
        h = prev[-1] if prev else "(unknown)"
        strongs = [(sm.start(), re.sub(r'<[^>]+>', '', sm.group(1)).strip())
                   for sm in re.finditer(r'<strong[^>]*>(.+?)</strong>',
                                         storage[:pos], re.DOTALL)]
        sub = strongs[-1][1] if strongs else ""
        return f"{h} / {sub}" if sub and sub != h else h

    results, seen = [], set()
    for m in re.finditer(r'data-datasource="([^"]+)"', storage):
        try:
            ds = json.loads(html.unescape(m.group(1)))
        except json.JSONDecodeError:
            continue
        jql = ds.get("parameters", {}).get("jql") or ds.get("jql")
        if jql and jql not in seen:
            seen.add(jql)
            results.append((nearest_heading(m.start()), jql))
    for m in re.finditer(
        r'<ac:parameter\s+ac:name="(?:jqlQuery|jql)"[^>]*>([^<]+)</ac:parameter>',
        storage,
    ):
        jql = html.unescape(m.group(1))
        if jql not in seen:
            seen.add(jql)
            results.append((nearest_heading(m.start()), jql))
    return results


def search_issues(jql, auth):
    issues, next_token = [], None
    while True:
        params = {"jql": jql, "fields": "id,key,summary,status,issuetype,subtasks", "maxResults": 100}
        if next_token:
            params["nextPageToken"] = next_token
        r = requests.get(
            f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3/search/jql",
            params=params, auth=auth, headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        issues.extend(data.get("issues", []))
        next_token = data.get("nextPageToken")
        if not next_token or data.get("isLast"):
            break
    return issues


def get_prs(issue_id, auth):
    # oAuth-com.github.integration.production: GitHub cloud integration after
    # Atlassian migrated it to the OAuth-based app (observed 2026-07)
    for app_type in ("oAuth-com.github.integration.production",
                     "GitHub", "githubEnterpriseServer"):
        r = requests.get(
            f"https://{JIRA_DOMAIN}/rest/dev-status/latest/issue/detail",
            params={"issueId": issue_id, "applicationType": app_type,
                    "dataType": "pullrequest"},
            auth=auth, headers={"Accept": "application/json"},
        )
        if r.ok:
            detail = r.json().get("detail", [])
            if detail and detail[0].get("pullRequests"):
                return detail[0]["pullRequests"]
    return []


def get_prs_for_issue(issue, auth):
    """Get PRs directly on the issue; if none, fall back to sub-tasks.
    Returns (prs, source) where source is None for direct PRs, or a list
    of sub-task keys whose PRs were aggregated."""
    prs = get_prs(issue["id"], auth)
    if prs:
        return prs, None
    subtasks = issue["fields"].get("subtasks") or []
    if not subtasks:
        return [], None
    aggregated, source_keys = [], []
    for st in subtasks:
        st_prs = get_prs(st["id"], auth)
        if st_prs:
            aggregated.extend(st_prs)
            source_keys.append(st["key"])
    return aggregated, (source_keys or None)


def check(prs, status, issue_key, issuetype=None):
    project = issue_key.split("-")[0] if "-" in issue_key else ""
    if project in SKIP_PROJECTS:
        return "SKIP", f"Project {project} 略過（非 RD 工作）"
    if issuetype in SKIP_ISSUETYPES:
        return "SKIP", f"Issuetype {issuetype} 略過（父單，PR 在 sub-task）"
    if status in SKIP_STATUSES:
        return "SKIP", f"狀態為 {status}，略過"
    if not prs:
        return "FAIL", "No PR found"
    merged = [p for p in prs if p["status"] == "MERGED"]
    pending = [p for p in prs if p["status"] == "OPEN"]
    declined = [p for p in prs if p["status"] == "DECLINED"]
    if merged:
        # 至少 1 PR 已 merged → 視為已上版。剩下 OPEN 通常是 follow-up，DECLINED 是廢棄。
        ignored = []
        if pending:
            ignored.append(f"{len(pending)} open")
        if declined:
            ignored.append(f"{len(declined)} declined")
        note = f" ({', '.join(ignored)} ignored)" if ignored else ""
        return "PASS", f"{len(merged)} PR(s) merged{note}"
    if pending:
        return "WARN", (f"{len(pending)} PR(s) still OPEN: "
                        + ", ".join(p.get("url", "?") for p in pending))
    return "FAIL", f"All {len(declined)} PR(s) DECLINED"


def run_section(jql, auth, section_name):
    issues = search_issues(jql, auth)
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "SKIP": "⏭️ "}
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    rows = []
    print(f"\n[{section_name}] ({len(issues)} tickets)")
    print(f"JQL: {jql}\n")
    for issue in issues:
        prs, from_subtasks = get_prs_for_issue(issue, auth)
        status = issue["fields"]["status"]["name"]
        issuetype = issue["fields"].get("issuetype", {}).get("name")
        verdict, msg = check(prs, status, issue["key"], issuetype)
        counts[verdict] += 1
        summary = issue["fields"]["summary"]
        rows.append({
            "key": issue["key"], "status": status, "summary": summary,
            "verdict": verdict, "msg": msg, "prs": prs, "from_subtasks": from_subtasks,
        })
        print(f"  {icon[verdict]} {issue['key']:14s} [{status:18s}] {summary[:55]}")
        if from_subtasks:
            print(f"      ↳ PRs aggregated from sub-tasks: {', '.join(from_subtasks)}")
        if verdict in ("FAIL", "WARN"):
            print(f"      → {msg}")
    print(f"\nSummary: {counts['PASS']} passed / {counts['FAIL']} failed / "
          f"{counts['WARN']} warned / {counts['SKIP']} skipped")
    return {"name": section_name, "jql": jql, "counts": counts, "rows": rows}


# ------------------------- Confluence report sub-page -------------------------

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _jira_link(key):
    return f'<a href="https://{JIRA_DOMAIN}/browse/{_esc(key)}">{_esc(key)}</a>'


def build_report_html(page_url, all_sections, when):
    """Build Confluence storage XHTML for the PR check report sub-page."""
    parts = []
    parts.append(f'<p>Generated at <strong>{_esc(when.strftime("%Y-%m-%d %H:%M:%S"))}</strong></p>')
    parts.append(f'<p>Source page: <a href="{_esc(page_url)}">{_esc(page_url)}</a></p>')

    # ---- Summary table ----
    total = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    parts.append("<h2>Summary</h2>")
    parts.append('<table><tbody>')
    parts.append("<tr><th>Section</th><th>Total</th><th>✅ Pass</th>"
                 "<th>❌ Fail</th><th>⚠️ Warn</th><th>⏭️ Skip</th></tr>")
    for s in all_sections:
        c = s["counts"]
        for k in c: total[k] += c[k]
        n = sum(c.values())
        parts.append(f"<tr><td>{_esc(s['name'])}</td><td>{n}</td>"
                     f"<td>{c['PASS']}</td><td>{c['FAIL']}</td>"
                     f"<td>{c['WARN']}</td><td>{c['SKIP']}</td></tr>")
    parts.append(f"<tr><td><strong>TOTAL</strong></td>"
                 f"<td><strong>{sum(total.values())}</strong></td>"
                 f"<td><strong>{total['PASS']}</strong></td>"
                 f"<td><strong>{total['FAIL']}</strong></td>"
                 f"<td><strong>{total['WARN']}</strong></td>"
                 f"<td><strong>{total['SKIP']}</strong></td></tr>")
    parts.append("</tbody></table>")

    # ---- Attention items (deduplicated) ----
    warn_rows, fail_rows = {}, {}
    for s in all_sections:
        for r in s["rows"]:
            if r["verdict"] == "WARN":
                warn_rows.setdefault(r["key"], r)
            elif r["verdict"] == "FAIL":
                fail_rows.setdefault(r["key"], r)

    if warn_rows:
        parts.append("<h2>⚠️ WARN — PR 仍 OPEN（需追進度）</h2><ul>")
        for key, r in warn_rows.items():
            pending = [p for p in r["prs"] if p.get("status") == "OPEN"]
            pr_html = " ".join(
                f'<a href="{_esc(p["url"])}">PR #{_esc(p.get("id","?"))}</a>'
                for p in pending
            )
            parts.append(f'<li>{_jira_link(key)} [{_esc(r["status"])}] '
                         f'{_esc(r["summary"])} — {pr_html}</li>')
        parts.append("</ul>")

    if fail_rows:
        parts.append("<h2>❌ FAIL — 查不到 PR（請找 owner 確認）</h2><ul>")
        for key, r in fail_rows.items():
            parts.append(f'<li>{_jira_link(key)} [{_esc(r["status"])}] '
                         f'{_esc(r["summary"])}</li>')
        parts.append("</ul>")

    # ---- Per-section details (FAIL/WARN/SKIP only; PASS items omitted as noise) ----
    sections_with_issues = [
        s for s in all_sections
        if any(r["verdict"] != "PASS" for r in s["rows"])
    ]
    if sections_with_issues:
        parts.append("<h2>Per-section details (excluding PASS)</h2>")
        icon = {"FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}
        for s in sections_with_issues:
            non_pass = [r for r in s["rows"] if r["verdict"] != "PASS"]
            c = s["counts"]
            title = (f"{s['name']} — {c['FAIL']} fail / {c['WARN']} warn / "
                     f"{c['SKIP']} skip (of {sum(c.values())} total)")
            parts.append('<ac:structured-macro ac:name="expand">')
            parts.append(f'<ac:parameter ac:name="title">{_esc(title)}</ac:parameter>')
            parts.append('<ac:rich-text-body>')
            parts.append(f'<p><code>{_esc(s["jql"])}</code></p>')
            parts.append('<table><tbody>')
            parts.append("<tr><th></th><th>Ticket</th><th>Status</th>"
                         "<th>Summary</th><th>Note</th></tr>")
            for r in non_pass:
                note_parts = [_esc(r["msg"])]
                if r["from_subtasks"]:
                    links = ", ".join(_jira_link(k) for k in r["from_subtasks"])
                    note_parts.append(f"↳ sub-tasks: {links}")
                note = "<br/>".join(note_parts)
                parts.append(f"<tr><td>{icon[r['verdict']]}</td>"
                             f"<td>{_jira_link(r['key'])}</td>"
                             f"<td>{_esc(r['status'])}</td>"
                             f"<td>{_esc(r['summary'])}</td>"
                             f"<td>{note}</td></tr>")
            parts.append('</tbody></table>')
            parts.append('</ac:rich-text-body>')
            parts.append('</ac:structured-macro>')

    return "\n".join(parts)


def create_subpage(parent_page_id, title, body_html, auth):
    """Create a child page under parent_page_id. Returns the new page dict."""
    # v2 API needs spaceId, not spaceKey — fetch it from the parent
    r = requests.get(
        f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}/wiki/api/v2/pages/{parent_page_id}",
        auth=auth, headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    space_id = r.json()["spaceId"]

    r = requests.post(
        f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}/wiki/api/v2/pages",
        json={
            "spaceId": space_id,
            "parentId": parent_page_id,
            "title": title,
            "body": {"representation": "storage", "value": body_html},
            "status": "current",
        },
        auth=auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    r.raise_for_status()
    return r.json()


def lookup_account_ids(emails, auth):
    """Resolve emails → accountIds via Jira user search.
    Returns list of accountIds for emails that matched; prints a warning for ones that didn't."""
    account_ids = []
    for email in emails:
        r = requests.get(
            f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3/user/search",
            params={"query": email},
            auth=auth, headers={"Accept": "application/json"},
        )
        users = r.json() if r.ok else []
        # Prefer exact email match (case-insensitive)
        matched = next(
            (u for u in users
             if (u.get("emailAddress") or "").lower() == email.lower()),
            None,
        )
        if not matched and users:
            # Email may be hidden by privacy settings; fallback to first hit
            matched = users[0]
            print(f"[warn] Email not visible for {email}; using first match "
                  f"({matched.get('displayName')!r}, {matched['accountId']})")
        if matched:
            account_ids.append(matched["accountId"])
        else:
            print(f"[warn] No Atlassian user found for {email!r} — skipping")
    return account_ids


def restrict_page(page_id, account_ids, auth):
    """Restrict view+edit on page to only the given accountIds.
    Uses Confluence v1 restriction API (v2 doesn't yet expose PUT for restrictions)."""
    user_list = [{"type": "known", "accountId": aid} for aid in account_ids]
    body = [
        {"operation": "read",   "restrictions": {"user": user_list, "group": []}},
        {"operation": "update", "restrictions": {"user": user_list, "group": []}},
    ]
    r = requests.put(
        f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}/wiki/rest/api/content/{page_id}/restriction",
        json=body, auth=auth,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    if not r.ok:
        print(f"[warn] Failed to set restrictions: HTTP {r.status_code} — {r.text[:200]}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Check whether all Jira tickets in a Confluence release page have merged PRs.",
    )
    parser.add_argument("url", help="Confluence release page URL")
    parser.add_argument(
        "-s", "--section",
        default=None,
        help="Only check sections whose heading contains this string (case-insensitive). "
             "Omit to check all sections.",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="Skip creating the Confluence sub-page record (default: create one under the release page).",
    )
    parser.add_argument(
        "-r", "--restrict-to",
        default=None,
        help="Comma-separated emails to restrict the sub-page to (view + edit). "
             "Creator's email is always included to avoid lockout. "
             "Falls back to RESTRICT_TO env var.",
    )
    args = parser.parse_args()

    auth = get_auth()
    page_id = get_page_id(args.url)
    storage = fetch_storage(page_id, auth)
    sections = extract_datasource_jqls(storage)
    print(f"Detected {len(sections)} JQL sections")
    matched = 0
    all_results = []
    for heading, jql in sections:
        if args.section and args.section.lower() not in heading.lower():
            continue
        matched += 1
        all_results.append(run_section(jql, auth, heading))
    if args.section and matched == 0:
        print(f"\n[warn] No sections matched filter: {args.section!r}")
        return

    # Create Confluence sub-page record (unless --no-record)
    if args.no_record:
        print("\n[info] --no-record set, skipping Confluence sub-page creation.")
        return
    when = datetime.now()
    filter_suffix = f" ({args.section})" if args.section else ""
    title = f"PR Check - {when.strftime('%Y-%m-%d %H:%M:%S')}{filter_suffix}"
    body = build_report_html(args.url, all_results, when)
    try:
        page = create_subpage(page_id, title, body, auth)
        webui = page.get("_links", {}).get("webui") or ""
        url = (f"https://{JIRA_DOMAIN}/wiki{webui}" if webui.startswith("/")
               else webui or f"(page id {page.get('id')})")
        print(f"\n✅ Report saved: {url}")
    except requests.HTTPError as e:
        print(f"\n[warn] Failed to create Confluence sub-page: {e}")
        print(f"       Response: {e.response.text[:300] if e.response else '(no body)'}")
        print(f"       (run with --no-record to suppress this attempt)")
        return

    # Apply view/edit restrictions if requested
    restrict_csv = args.restrict_to or os.environ.get("RESTRICT_TO")
    if restrict_csv:
        emails = [e.strip() for e in restrict_csv.split(",") if e.strip()]
        # Always include the creator (current user) so we don't lock ourselves out
        if auth[0] and auth[0] not in emails:
            emails.append(auth[0])
        account_ids = lookup_account_ids(emails, auth)
        if account_ids:
            if restrict_page(page["id"], account_ids, auth):
                print(f"🔒 Restricted to {len(account_ids)} user(s): {', '.join(emails)}")
        else:
            print(f"[warn] No accountIds resolved; page left unrestricted")


if __name__ == "__main__":
    main()
