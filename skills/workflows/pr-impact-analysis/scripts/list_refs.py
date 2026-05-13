#!/usr/bin/env python3
"""Mode A list-refs: 列 tags / release branches / dev branches for kkday-it/<repo>.

完全用本地 `gh` CLI（不打後端 /get-github-refs，避免共用 token + rate limit）。
SKILL 不要再手寫 gh 指令——一律呼叫這支 script。

Output 範例：
  === kkday-b2c-api ===

  Tags (10 / 30, 用 --tags-limit N / --show-all-tags 看更多):
    T1.  ...
    ...

  Release Branches (固定列):
    R1.  master
    R2.  develop
    R3.  release/2.52.7
    ...

  Dev Branches (10 / 22, 用 --branches-limit N / --show-all-branches):
    B1.  ...
    ...

  請輸入 base 跟 target，例如：T2 T1 / R1 B1 / v3.5.6 master / B1 R1
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

OWNER = "kkday-it"

RELEASE_LIKE_RE = re.compile(r"^(master|main|develop|rc|release/.*|hotfix/.*)$")
# 一定要列出來的「主分支」——即使不在 top-100 commit date ranking 裡也要查
ALWAYS_LIST = ("master", "main", "develop")


def _gh(args: list[str], check: bool = True) -> str:
    """Run `gh <args>` and return stdout. Raise CalledProcessError on failure."""
    res = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and res.returncode != 0:
        sys.stderr.write(f"[list_refs] gh {' '.join(args)} failed (rc={res.returncode}): {res.stderr}\n")
        raise subprocess.CalledProcessError(res.returncode, args, res.stdout, res.stderr)
    return res.stdout


def fetch_tags(repo: str, per_page: int = 30) -> list[str]:
    """Fetch latest N tags via REST API."""
    out = _gh(
        [
            "api",
            f"repos/{OWNER}/{repo}/tags?per_page={per_page}",
            "--jq",
            ".[].name",
        ],
        check=False,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def fetch_branches_graphql(repo: str) -> list[dict[str, Any]]:
    """Fetch top-100 branches via GraphQL ordered by latest commit date.

    Returns list of {"name": str, "committedDate": str|None}.
    `first:100` is the GraphQL upper limit; `first:200` triggers EXCESSIVE_PAGINATION.
    Do NOT use --jq here — it outputs NDJSON (one object per line) which breaks json.load.
    """
    query = """
    query($owner:String!,$name:String!){
      repository(owner:$owner,name:$name){
        refs(refPrefix:"refs/heads/",first:100,orderBy:{field:TAG_COMMIT_DATE,direction:DESC}){
          nodes{name target{... on Commit{committedDate}}}
        }
      }
    }
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fp:
        fp.write("{}")  # placeholder
        tmp_path = fp.name

    raw = _gh(
        [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={OWNER}",
            "-f",
            f"name={repo}",
        ],
        check=False,
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[list_refs] GraphQL response parse failed: {e}\nraw head:\n{raw[:500]}\n")
        return []

    nodes = (
        data.get("data", {})
        .get("repository", {})
        .get("refs", {})
        .get("nodes", [])
        or []
    )
    out = []
    for n in nodes:
        name = n.get("name")
        target = n.get("target") or {}
        committed = target.get("committedDate")
        if name:
            out.append({"name": name, "committedDate": committed})
    return out


def fetch_branch_meta(repo: str, name: str) -> dict[str, Any] | None:
    """Direct fetch a single branch (used as fallback for master/main/develop
    that may be ranked below top-100 by commit date)."""
    try:
        out = _gh(
            ["api", f"repos/{OWNER}/{repo}/branches/{name}"],
            check=False,
        )
        if not out.strip():
            return None
        data = json.loads(out)
        # branches/<name> response: {"name": ..., "commit": {"commit": {"author": {"date": ...}}}}
        committed = (
            data.get("commit", {})
            .get("commit", {})
            .get("author", {})
            .get("date")
        )
        return {"name": data.get("name", name), "committedDate": committed}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def split_branches(
    branches: list[dict[str, Any]],
    repo: str,
    release_branches_keep: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (release_like, dev) and ensure master/main/develop always present.

    Release branches sorted by committedDate desc, capped to most-recent N (避免 76 row dump).
    Dev branches sorted by committedDate desc, full list returned (caller decides display limit).
    """
    release_like: list[dict[str, Any]] = []
    dev: list[dict[str, Any]] = []
    seen_names = set()
    for b in branches:
        name = b["name"]
        if name in seen_names:
            continue
        seen_names.add(name)
        if RELEASE_LIKE_RE.match(name):
            release_like.append(b)
        else:
            dev.append(b)

    # 補上 master/main/develop（如果不在 top-100 GraphQL 結果裡）
    missing = [n for n in ALWAYS_LIST if n not in seen_names]
    if missing:
        with ThreadPoolExecutor(max_workers=len(missing)) as ex:
            futures = {ex.submit(fetch_branch_meta, repo, n): n for n in missing}
            for fut in as_completed(futures):
                meta = fut.result()
                if meta is not None:
                    release_like.append(meta)
                    seen_names.add(meta["name"])

    # 排序：committedDate desc，None 排最後
    def _key(x):
        return x.get("committedDate") or ""

    release_like.sort(key=_key, reverse=True)
    dev.sort(key=_key, reverse=True)

    # 主分支（master/main/develop）強制排在最前面
    primary = [b for b in release_like if b["name"] in ALWAYS_LIST]
    others = [b for b in release_like if b["name"] not in ALWAYS_LIST]
    # release/* 太多時砍掉只留最近 N 個
    if len(others) > release_branches_keep:
        others = others[:release_branches_keep]
    release_like = primary + others

    return release_like, dev


def short_date(committed: str | None) -> str:
    if not committed:
        return ""
    return committed[:10]  # YYYY-MM-DD


def apply_filter(items: list[Any], pattern: str, key=lambda x: x) -> list[Any]:
    if not pattern:
        return items
    p = pattern.lower()
    return [x for x in items if p in str(key(x)).lower()]


def render(
    repo: str,
    tags: list[str],
    release_like: list[dict[str, Any]],
    dev: list[dict[str, Any]],
    tags_limit: int,
    branches_limit: int,
    show_all_tags: bool,
    show_all_branches: bool,
    prefix_t: str = "T",
    prefix_r: str = "R",
    prefix_b: str = "B",
    section_title: str | None = None,
) -> str:
    lines: list[str] = []
    title = section_title or f"=== {OWNER}/{repo} ==="
    lines.append(title)
    lines.append("")

    # Tags
    tags_total = len(tags)
    tags_shown = tags if show_all_tags else tags[:tags_limit]
    tags_hint = (
        f"Tags ({len(tags_shown)} / {tags_total}, 用 --tags-limit N / --show-all-tags 看更多):"
        if not show_all_tags
        else f"Tags ({tags_total}):"
    )
    lines.append(tags_hint)
    for i, t in enumerate(tags_shown, 1):
        lines.append(f"  {prefix_t}{i}.  {t}")
    if not tags_shown:
        lines.append("  (無 tag)")
    lines.append("")

    # Release Branches
    lines.append("Release Branches (固定列):")
    for i, b in enumerate(release_like, 1):
        d = short_date(b.get("committedDate"))
        d_str = f"  ({d})" if d else ""
        lines.append(f"  {prefix_r}{i}.  {b['name']}{d_str}")
    if not release_like:
        lines.append("  (無 release branch)")
    lines.append("")

    # Dev Branches
    dev_total = len(dev)
    dev_shown = dev if show_all_branches else dev[:branches_limit]
    dev_hint = (
        f"Dev Branches ({len(dev_shown)} / {dev_total}, 用 --branches-limit N / --show-all-branches):"
        if not show_all_branches
        else f"Dev Branches ({dev_total}):"
    )
    lines.append(dev_hint)
    for i, b in enumerate(dev_shown, 1):
        d = short_date(b.get("committedDate"))
        d_str = f"  ({d})" if d else ""
        lines.append(f"  {prefix_b}{i}.  {b['name']}{d_str}")
    if not dev_shown:
        lines.append("  (無 dev branch)")

    return "\n".join(lines)


def gh_auth_check() -> None:
    res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(
            "[list_refs] gh auth status 失敗。請先跑：gh auth login\n"
            f"stderr: {res.stderr}\n"
        )
        sys.exit(2)


def list_one_repo(
    repo: str,
    args: argparse.Namespace,
    prefix_t: str = "T",
    prefix_r: str = "R",
    prefix_b: str = "B",
    section_title: str | None = None,
) -> str:
    tags = fetch_tags(repo, per_page=30)
    branches = fetch_branches_graphql(repo)
    release_like, dev = split_branches(branches, repo)

    if args.filter:
        tags = apply_filter(tags, args.filter)
        release_like = apply_filter(release_like, args.filter, key=lambda x: x["name"])
        dev = apply_filter(dev, args.filter, key=lambda x: x["name"])

    return render(
        repo=repo,
        tags=tags,
        release_like=release_like,
        dev=dev,
        tags_limit=args.tags_limit,
        branches_limit=args.branches_limit,
        show_all_tags=args.show_all_tags,
        show_all_branches=args.show_all_branches,
        prefix_t=prefix_t,
        prefix_r=prefix_r,
        prefix_b=prefix_b,
        section_title=section_title,
    )


# 別名 → repo name；member-ci 系列要同時列 b2c-web（W 系列）
ALIASES = {
    "ios": "kkday-ios-member",
    "android": "kkday-android-member",
    "member-ci": "kkday-member-ci",
    "mobile-member-ci": "kkday-mobile-member-ci",
    "b2c-web": "kkday-b2c-web",
    "b2c-api": "kkday-b2c-api",
}
NEEDS_B2C_WEB_PAIR = {"member-ci", "mobile-member-ci"}


def resolve_repo(token: str) -> tuple[str, bool]:
    """Resolve user input to repo name. Returns (repo_name, needs_b2c_web_pairing)."""
    if token in ALIASES:
        return ALIASES[token], token in NEEDS_B2C_WEB_PAIR
    # 容錯：使用者寫 owner/repo → 截後段
    if "/" in token:
        return token.split("/")[-1], False
    return token, False


def main() -> int:
    ap = argparse.ArgumentParser(description="List GitHub refs for Mode A (tags / release / dev branches).")
    ap.add_argument("repo", help="repo name (e.g. kkday-b2c-api) or alias (ios/android/member-ci/mobile-member-ci/b2c-web/b2c-api)")
    ap.add_argument("--tags-limit", type=int, default=10, help="how many tags to display (default 10)")
    ap.add_argument("--branches-limit", type=int, default=10, help="how many dev branches to display (default 10)")
    ap.add_argument("--show-all-tags", action="store_true", help="display all fetched tags (up to 30)")
    ap.add_argument("--show-all-branches", action="store_true", help="display all fetched dev branches")
    ap.add_argument("--filter", default="", help="case-insensitive substring filter applied to tags/branches")
    ap.add_argument("--skip-auth-check", action="store_true", help="skip gh auth status check")
    args = ap.parse_args()

    if not args.skip_auth_check:
        gh_auth_check()

    repo, needs_pair = resolve_repo(args.repo)
    out = list_one_repo(repo, args)
    sys.stdout.write(out + "\n")

    if needs_pair:
        sys.stdout.write("\n")
        # b2c-web 用 W 系列編號（保留 T2/R2/B2 給主 repo）
        pair_out = list_one_repo(
            "kkday-b2c-web",
            args,
            prefix_t="WT",
            prefix_r="WR",
            prefix_b="WB",
            section_title="=== kkday-it/kkday-b2c-web (web/mweb pairing) ===",
        )
        sys.stdout.write(pair_out + "\n")
        sys.stdout.write(
            "\n注意：member-ci / mobile-member-ci 場景請挑兩組 base/target（主 repo + b2c-web）。\n"
        )

    sys.stdout.write(
        "\n請輸入 base 跟 target，例如：T2 T1 / R1 B1 / v3.5.6 master / B1 R1\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())