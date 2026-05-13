#!/usr/bin/env python3
"""
release-impact-analysis pipeline runner

依序跑 ai-studio backend 的 release-impact 5 支 API：
  1. POST /get-diff                    (REST)
  2. POST /analyze-components          (SSE)
  3. POST /get-version-impact-summary  (SSE)
  4. POST /get-test-cases              (SSE)   [cycle != none 時]
  5. POST /ai-analyze-impact           (SSE)   [cycle != none 時]

跑的過程把當下狀態寫進 --out JSON，所以 Claude 隨時可以讀到進度 / 部分結果 / 最終結果。

用法：
  python3 run_pipeline.py \\
    --task-id <id> \\
    --repo kkday-ios-member \\
    --base 1.202.0/1.202.0.10 \\
    --target 1.203.0/1.203.0.3 \\
    --cycle KQT-R1359 \\
    --backend http://autotest-service.sit.kkday.com:8081/ai_studio \\
    --user cli-eden.lai@kkday.com \\
    --out /tmp/release_impact_<task-id>.json

`--cycle none` 跳過 step 4-5。

Exit code: 0 成功 / 1 任何 step 失敗（json status=failed 已寫入）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests


# ---- gh CLI helpers ----------------------------------------------------------
#
# 所有 GitHub call 都本地用 gh CLI（gh auth 由使用者 ~/.config/gh/hosts.yml 管），
# token 不離開使用者本機。後端不打 GitHub，避免共用 GITHUB_TOKEN rate limit 被吃爆。

_GH_REPO_OWNER = "kkday-it"


def _gh_api(path: str, timeout: int = 30) -> Any:
    """呼叫 `gh api <path>`，回 JSON。失敗 raise RuntimeError。"""
    cmd = ["gh", "api", path]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh api failed [{path}]: rc={result.returncode}, stderr={result.stderr.strip()[:300]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"gh api [{path}] returned non-JSON: {e}") from e


_CHANGE_TYPE_MAP = {
    "added": "added",
    "modified": "modified",
    "removed": "deleted",
    "renamed": "modified",
}


def _extract_author(commit_info: Dict[str, Any]) -> str:
    """從 GitHub commit response 抽 author name（對齊後端 fetch_commit_files 的邏輯）。"""
    commit_d = commit_info.get("commit") or {}
    name = (commit_d.get("author") or {}).get("name") or ""
    if not name:
        name = (commit_d.get("committer") or {}).get("name") or ""
    if not name:
        author_info = commit_info.get("author")
        if isinstance(author_info, dict):
            name = author_info.get("name") or author_info.get("login") or ""
        elif isinstance(author_info, str):
            name = author_info
    return name or "未知"


def _fetch_commit_files(repo: str, sha: str) -> List[Dict[str, str]]:
    """撈單一 commit 的 files + patch。失敗回空 list（保持 pipeline 不中斷）。"""
    try:
        detail = _gh_api(f"repos/{_GH_REPO_OWNER}/{repo}/commits/{sha}", timeout=20)
    except Exception:
        return []
    files: List[Dict[str, str]] = []
    for f in detail.get("files") or []:
        files.append(
            {
                "path": f.get("filename", ""),
                "changeType": _CHANGE_TYPE_MAP.get(f.get("status", ""), "modified"),
                "patch": f.get("patch", "") or "",
            }
        )
    return files


def fetch_compare_via_gh(
    repo: str, base: str, target: str, state: "State"
) -> Dict[str, Any]:
    """本地用 gh CLI 撈完整 compare data，組成後端 `prefetched_compare` 期望結構。

    Returns:
        {
          "diff": {"baseRef", "targetRef", "commits": [{"sha", "message", "author", "files": [{"path", "changeType", "patch"}]}]},
          "total_files": int,
          "ahead_by": int,
          "behind_by": int,
          "total_commits": int,
        }
    """
    step = "step0_local_compare"
    state.set(current_step=step)
    state.log_progress(step, f"gh api compare/{base}...{target}")

    compare_data = _gh_api(
        f"repos/{_GH_REPO_OWNER}/{repo}/compare/{base}...{target}", timeout=60
    )
    ahead_by = compare_data.get("ahead_by", 0)
    behind_by = compare_data.get("behind_by", 0)
    total_files = compare_data.get("total_files", 0)
    total_commits = compare_data.get("total_commits", 0)
    commits_data: List[Dict[str, Any]] = compare_data.get("commits", []) or []
    actual_count = len(commits_data)

    state.log_progress(
        step,
        f"ahead_by={ahead_by} behind_by={behind_by} total_files={total_files} commits={actual_count}/{total_commits}",
    )

    # 對齊後端：commits > 250 時 compare API 只回 250，要 paginate commits API
    if total_commits > 250 and actual_count == 250:
        state.log_progress(
            step, f"commits 超過 250，改用 commits API paginate (target_needed={ahead_by or total_commits})"
        )
        base_sha = (compare_data.get("base_commit") or {}).get("sha") or ""
        merge_base_sha = (compare_data.get("merge_base_commit") or {}).get("sha") or ""
        target_clean = target.replace("heads/", "").replace("refs/heads/", "")
        needed = ahead_by if ahead_by > 0 else total_commits
        all_commits: List[Dict[str, Any]] = []
        page = 1
        per_page = 100
        max_pages = (needed // per_page) + 2
        while page <= max_pages and len(all_commits) < needed:
            try:
                page_commits = _gh_api(
                    f"repos/{_GH_REPO_OWNER}/{repo}/commits?sha={target_clean}&per_page={per_page}&page={page}",
                    timeout=60,
                )
            except Exception as e:
                state.log_progress(step, f"WARN: commits page {page} failed: {e}")
                break
            if not page_commits:
                break
            stop = False
            for c in page_commits:
                csha = c.get("sha") or ""
                if csha == base_sha or csha == merge_base_sha:
                    stop = True
                    break
                all_commits.append(c)
                if len(all_commits) >= needed or len(all_commits) >= 1000:
                    stop = True
                    break
            state.log_progress(
                step,
                f"paginate page {page} → collected {len(all_commits)} / needed {needed}",
            )
            if stop or len(page_commits) < per_page:
                break
            page += 1
        if len(all_commits) > actual_count:
            commits_data = all_commits

    # 並行撈每個 commit 的 files + patch
    state.log_progress(
        step, f"撈 {len(commits_data)} 個 commit 的 files（max_workers=20）"
    )
    sha_to_files: Dict[str, List[Dict[str, str]]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_sha = {
            executor.submit(_fetch_commit_files, repo, c.get("sha") or ""): c.get("sha")
            or ""
            for c in commits_data
            if c.get("sha")
        }
        for future in as_completed(future_to_sha):
            sha = future_to_sha[future]
            try:
                sha_to_files[sha] = future.result()
            except Exception as e:
                state.log_progress(step, f"WARN: commit {sha[:8]} files failed: {e}")
                sha_to_files[sha] = []
            done += 1
            if done % 25 == 0:
                state.log_progress(step, f"commits files 進度 {done}/{len(future_to_sha)}")

    commits: List[Dict[str, Any]] = []
    for c in commits_data:
        sha = c.get("sha") or ""
        commits.append(
            {
                "sha": sha,
                "message": ((c.get("commit") or {}).get("message") or "").split("\n")[0],
                "author": _extract_author(c),
                "files": sha_to_files.get(sha, []),
            }
        )

    state.log_progress(step, f"compare 完成，commits={len(commits)}")
    return {
        "diff": {
            "baseRef": base,
            "targetRef": target,
            "commits": commits,
        },
        "total_files": total_files,
        "ahead_by": ahead_by,
        "behind_by": behind_by,
        "total_commits": total_commits,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def emit(step: str, message: str) -> None:
    """Emit one-line progress to stdout (for Monitor tool to stream)."""
    print(f"[{now_iso()}] [{step}] {message}", flush=True)


class State:
    """Shared mutable state, persisted to JSON after every meaningful change."""

    def __init__(self, out: Path, params: dict[str, Any]):
        self.out = out
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {
            "task_id": params["task_id"],
            "status": "running",
            "current_step": "init",
            "params": params,
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "completed_at": None,
            "diff_meta": None,
            "impacted": None,
            "impact_summary": None,
            "test_cases": None,            # flat 合併（向後相容）
            "ai_results": None,            # flat 合併（向後相容）
            "test_cases_by_cycle": None,   # {group_label: {"cycle_id": "...", "test_cases": [...]}}
            "ai_results_by_cycle": None,   # {group_label: {"cycle_id": "...", "results": [...]}}
            "cycles_resolved": None,       # [{"group": "...", "cycle_id": "..."}]
            "automated_case_ids": None,
            "progress_log": [],
            "errors": [],
        }
        self._flush_locked()

    def set(self, **kwargs: Any) -> None:
        with self._lock:
            self.data.update(kwargs)
            self.data["updated_at"] = now_iso()
            self._flush_locked()

    def log_progress(self, step: str, message: str) -> None:
        with self._lock:
            self.data["progress_log"].append(
                {"ts": now_iso(), "step": step, "message": message}
            )
            # 只保留最近 100 條 progress（避免檔案無限長）
            self.data["progress_log"] = self.data["progress_log"][-100:]
            self.data["updated_at"] = now_iso()
            self._flush_locked()
        emit(step, message)

    def fail(self, step: str, error: str) -> None:
        with self._lock:
            self.data["errors"].append(
                {"ts": now_iso(), "step": step, "error": error}
            )
            self.data["status"] = "failed"
            self.data["completed_at"] = now_iso()
            self.data["updated_at"] = now_iso()
            self._flush_locked()
        emit(step, f"FAILED — {error}")

    def complete(self) -> None:
        with self._lock:
            self.data["status"] = "completed"
            self.data["current_step"] = "done"
            self.data["completed_at"] = now_iso()
            self.data["updated_at"] = now_iso()
            self._flush_locked()
        emit("done", "pipeline completed")

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Write JSON atomically. Caller must hold self._lock."""
        tmp = self.out.with_suffix(self.out.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        tmp.replace(self.out)


def parse_sse_data_line(line: str) -> dict[str, Any] | None:
    """
    後端 SSE 兩種格式：
      data: {...}
      event: progress\ndata: {...}
    這裡只看 data: 那行（event: 行不影響 payload 內 type 欄位）。
    """
    if not line.startswith("data: "):
        return None
    raw = line[6:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def stream_sse(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    state: State,
    step: str,
    timeout: int = 1800,
) -> dict[str, Any]:
    """
    打 SSE endpoint，遇 progress 就 log，遇 result 就回傳 data。
    遇 end 或 stream 結束就 break。
    """
    state.set(current_step=step)
    state.log_progress(step, f"連線 {url}")

    result_data: dict[str, Any] | None = None
    try:
        with requests.post(
            url, json=payload, headers=headers, stream=True, timeout=timeout
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                payload_obj = parse_sse_data_line(raw_line)
                if payload_obj is None:
                    continue
                ptype = payload_obj.get("type")
                if ptype == "progress":
                    msg = payload_obj.get("message", "")
                    state.log_progress(step, msg)
                elif ptype == "result":
                    if payload_obj.get("status") == "error":
                        raise RuntimeError(
                            payload_obj.get("message")
                            or payload_obj.get("data")
                            or "result status=error"
                        )
                    result_data = payload_obj.get("data") or {}
                elif ptype == "end":
                    break
                elif ptype == "error":
                    raise RuntimeError(
                        payload_obj.get("message") or "stream error"
                    )
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP error in {step}: {e}") from e

    if result_data is None:
        raise RuntimeError(f"{step} stream ended without result event")
    return result_data


AUTOMATION_PLATFORM_BASE = "http://autotest-service.sit.kkday.com:8080"


# repo → {group: cycle_id}。group 跟 SKILL.md 的 Repo 別名表對齊。
# b2c-api 只有 regression，沒 project / trans。
CYCLE_MAP: dict[str, dict[str, str]] = {
    "kkday-ios-member": {
        "regression": "KQT-R1359",
        "project": "KQT-R1058",
        "trans": "KQT-R1192",
    },
    "kkday-android-member": {
        "regression": "KQT-R1360",
        "project": "KQT-R1057",
        "trans": "KQT-R1191",
    },
    "kkday-member-ci": {
        "regression": "KQT-R929",
        "project": "KQT-R1056",
        "trans": "KQT-R1189",
    },
    "kkday-mobile-member-ci": {
        "regression": "KQT-R928",
        "project": "KQT-R1055",
        "trans": "KQT-R1190",
    },
    "kkday-b2c-web": {
        "regression": "KQT-R929",
        "project": "KQT-R1056",
        "trans": "KQT-R1189",
    },
    "kkday-b2c-api": {
        "regression": "KQT-R1106",
    },
}


def resolve_cycles(repo: str, cycle_arg: str) -> list[tuple[str, str]]:
    """
    把 --cycle 參數解析成 [(group_label, cycle_id), ...]。

    cycle_arg 接受：
      - 空字串 / "default" / "all" → 跑 repo 對應的全部 cycle
      - "none" / "skip" → 回 [] （上游處理短路）
      - "regression" / "project" / "trans" → 單一 group
      - "KQT-R929" → 單一 cycle，group_label = "custom"
      - "KQT-R929,KQT-R1056" / "regression,trans" → 混合，逗號分隔
    """
    arg = (cycle_arg or "").strip().lower()
    repo_cycles = CYCLE_MAP.get(repo, {})

    if arg in ("none", "skip"):
        return []
    if arg in ("", "default", "all"):
        # 該 repo 的所有 cycle，按 regression → project → trans 順序
        order = ["regression", "project", "trans"]
        return [(g, repo_cycles[g]) for g in order if g in repo_cycles]

    out: list[tuple[str, str]] = []
    for token in [t.strip() for t in cycle_arg.split(",") if t.strip()]:
        low = token.lower()
        if low in ("regression", "project", "trans"):
            cid = repo_cycles.get(low)
            if cid:
                out.append((low, cid))
            # 沒對到（例如 b2c-api 沒 project）就跳過，上游 log
        elif token.upper().startswith("KQT-R"):
            # 反查 group label，若不在 map 中標 custom
            label = "custom"
            for g, cid in repo_cycles.items():
                if cid == token.upper():
                    label = g
                    break
            out.append((label, token.upper()))
        # 其他格式忽略
    return out


def fetch_automated_case_ids(state: State) -> list[str]:
    """
    從 automation platform 拿所有自動化 case 列表，回傳 case_id list。
    用來跟 MUST/SHOULD 比對誰能一鍵觸發 single test run。
    """
    step = "step6_fetch_automated_cases"
    state.set(current_step=step)
    state.log_progress(step, f"GET {AUTOMATION_PLATFORM_BASE}/api/v1/testcase")
    try:
        resp = requests.get(
            f"{AUTOMATION_PLATFORM_BASE}/api/v1/testcase",
            params={"case_id": ""},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        state.log_progress(step, f"WARN — 拿不到自動化 case list：{e}")
        return []

    rows = data.get("data") or []
    case_ids = sorted({r["case_id"] for r in rows if r.get("case_id")})
    state.log_progress(step, f"拿到 {len(case_ids)} 支自動化 case")
    return case_ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cycle", default="all")
    parser.add_argument(
        "--backend",
        default="http://autotest-service.sit.kkday.com:8081/ai_studio",
    )
    parser.add_argument("--user", default="cli-anonymous")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    backend = args.backend.rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": args.user,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    state = State(
        out=out_path,
        params={
            "task_id": args.task_id,  # local task id (JSON 檔名用)
            "repo": args.repo,
            "base": args.base,
            "target": args.target,
            "cycle": args.cycle,
            "backend": backend,
            "user": args.user,
        },
    )

    # ---- Step 1: get-diff (REST) ----
    # 一律本地透過 gh CLI 撈 GitHub compare（不打共用後端 token），
    # 抓到的 compare payload 透過 `prefetched_compare` 丟給後端，
    # 後端跳過所有 GitHub call，但仍跑 filter / hash / DB 寫入，
    # UI 才看得到完整 step1 結果。
    try:
        state.set(current_step="step1_get_diff")
        prefetched = fetch_compare_via_gh(args.repo, args.base, args.target, state)
    except Exception as e:
        state.fail("step1_get_diff", f"gh CLI compare 失敗：{e}")
        return 1

    try:
        state.log_progress("step1_get_diff", "POST /get-diff (帶 prefetched_compare)")
        r = requests.post(
            f"{backend}/api/release-impact/get-diff",
            json={
                "github_repo": args.repo,
                "base_ref": args.base,
                "target_ref": args.target,
                "prefetched_compare": prefetched,
            },
            headers=headers,
            timeout=120,
        )
        r.raise_for_status()
        diff_data = r.json()
    except Exception as e:
        state.fail("step1_get_diff", str(e))
        return 1

    diff = diff_data.get("diff") or {}
    # backend 在 step 1 自己 create_task，後續 step 必須用同一個 task_id
    # 才會把結果寫進同一筆 backend task，UI 才看得到完整 5 步紀錄。
    # 不用我們 --task-id (那只是 local JSON 檔名用)。
    backend_task_id = diff_data.get("task_id")
    diff_meta = {
        "ahead_by": diff_data.get("ahead_by"),
        "behind_by": diff_data.get("behind_by"),
        "total_files": diff_data.get("total_files"),
        "code_changed": diff_data.get("code_changed"),
        "release_only": diff_data.get("release_only"),
        "kept_files_count": len(diff_data.get("kept_files") or []),
        "ignored_files_count": len(diff_data.get("ignored_files") or []),
        "decision_reason": diff_data.get("decision_reason"),
        "diff_hash_or_files_hash": diff_data.get("diff_hash_or_files_hash"),
        "backend_task_id": backend_task_id,
    }
    state.set(diff_meta=diff_meta)
    if backend_task_id:
        state.log_progress(
            "step1_get_diff",
            f"backend task_id={backend_task_id} (UI 上可查)",
        )

    # 短路：沒程式碼變更 / 只有 release file
    if not diff_data.get("code_changed") or (
        diff_data.get("release_only") and not (diff_data.get("kept_files") or [])
    ):
        state.log_progress(
            "step1_get_diff",
            "code_changed=False or release_only — 短路結束",
        )
        state.complete()
        return 0

    diff_hash = diff_data.get("diff_hash_or_files_hash")

    # ---- Step 2: analyze-components (SSE) ----
    try:
        impacted_resp = stream_sse(
            f"{backend}/api/release-impact/analyze-components",
            payload={
                "diff": diff,
                "github_repo": args.repo,
                "base_ref": args.base,
                "target_ref": args.target,
                "task_id": backend_task_id,
            },
            headers=headers,
            state=state,
            step="step2_analyze_components",
        )
        impacted = impacted_resp.get("impacted") or impacted_resp
        state.set(impacted=impacted)
    except Exception as e:
        state.fail("step2_analyze_components", str(e))
        return 1

    # ---- Step 3: get-version-impact-summary (SSE) ----
    try:
        summary = stream_sse(
            f"{backend}/api/release-impact/get-version-impact-summary",
            payload={
                "diff": diff,
                "component_mapping": impacted,
                "diff_hash_or_files_hash": diff_hash,
                "github_repo": args.repo,
                "base_ref": args.base,
                "target_ref": args.target,
                "task_id": backend_task_id,
            },
            headers=headers,
            state=state,
            step="step3_version_impact_summary",
        )
        state.set(impact_summary=summary)
    except Exception as e:
        state.fail("step3_version_impact_summary", str(e))
        return 1

    # ---- 解析 cycle 列表 ----
    cycles = resolve_cycles(args.repo, args.cycle)
    state.set(cycles_resolved=[{"group": g, "cycle_id": c} for g, c in cycles])

    if not cycles:
        state.log_progress(
            "step4_get_test_cases",
            f"cycle={args.cycle} — 跳過 regression 分類（無 cycle 要跑）",
        )
        # 即便沒 cycle，仍跑 Step 6 拿 automated_case_ids（給後續其他流程參考）
        automated_case_ids = fetch_automated_case_ids(state)
        state.set(automated_case_ids=automated_case_ids)
        state.complete()
        return 0

    state.log_progress(
        "step4_get_test_cases",
        f"準備跑 {len(cycles)} 個 cycle："
        + ", ".join(f"{g}={c}" for g, c in cycles),
    )

    test_cases_by_cycle: dict[str, dict[str, Any]] = {}
    ai_results_by_cycle: dict[str, dict[str, Any]] = {}
    flat_test_cases: list[dict[str, Any]] = []
    flat_ai_results: list[dict[str, Any]] = []

    # ---- Step 4 & 5: 多 cycle 並行（每個 cycle 跑自己的 step4+step5 鏈）----
    # 為什麼並行：3 個 cycle 串行時 step4+5 佔總時間 95%，且彼此完全獨立（不同 cycle_id
    # 撈不同 case、各自跑 LLM）。並行 max(t1,t2,t3) 取代 t1+t2+t3，理論省 60%+。
    # 後端是 FastAPI + LLM，受限於 backend concurrency 與 LLM rate-limit，max_workers
    # 不要拉太高；目前 cycle 最多 3 個，直接綁定 cycle 數即可。
    impact_summary_snapshot = state.data["impact_summary"]

    def _run_one_cycle(
        group: str, cycle_id: str
    ) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]]]:
        """跑單一 cycle 的 step4+step5；回傳 (group, cycle_id, test_cases, results, errors)。"""
        step4_name = f"step4_get_test_cases[{group}]"
        try:
            tc_resp = stream_sse(
                f"{backend}/api/release-impact/get-test-cases",
                payload={
                    "test_cycle_id": cycle_id,
                    "task_id": backend_task_id,
                },
                headers=headers,
                state=state,
                step=step4_name,
            )
            test_cases = tc_resp.get("test_cases") or []
        except Exception as e:
            return group, cycle_id, [], [], [(step4_name, str(e))]

        if not test_cases:
            state.log_progress(
                step4_name,
                f"cycle={cycle_id} 沒撈到 case，跳過 step5",
            )
            return group, cycle_id, [], [], []

        step5_name = f"step5_ai_analyze_impact[{group}]"
        try:
            ai_resp = stream_sse(
                f"{backend}/api/release-impact/ai-analyze-impact",
                payload={
                    "impact_summary": impact_summary_snapshot,
                    "test_cases": test_cases,
                    "test_cycle_key": cycle_id,
                    "github_repo": args.repo,
                    "base_ref": args.base,
                    "target_ref": args.target,
                    "total_files": diff_meta.get("total_files"),
                    "ahead_by": diff_meta.get("ahead_by"),
                    "behind_by": diff_meta.get("behind_by"),
                    "diff_hash_or_files_hash": diff_hash,
                    "task_id": backend_task_id,
                },
                headers=headers,
                state=state,
                step=step5_name,
                timeout=3600,  # 大 cycle 可能跑很久
            )
            results = ai_resp.get("results") or []
        except Exception as e:
            return group, cycle_id, test_cases, [], [(step5_name, str(e))]

        return group, cycle_id, test_cases, results, []

    state.set(current_step=f"step4_5_parallel[{len(cycles)}_cycles]")
    state.log_progress(
        "step4_5_parallel",
        f"並行跑 {len(cycles)} 個 cycle (step4 + step5)",
    )

    cycle_outputs: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    cycle_errors: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, len(cycles))) as ex:
        futures = [ex.submit(_run_one_cycle, g, c) for g, c in cycles]
        for fut in as_completed(futures):
            group, cycle_id, test_cases, results, errs = fut.result()
            if errs:
                cycle_errors.extend(errs)
                continue
            cycle_outputs[group] = (test_cases, results)

    if cycle_errors:
        step_name, err = cycle_errors[0]
        extra = (
            f"; 另有 {len(cycle_errors) - 1} 個 cycle 也失敗"
            if len(cycle_errors) > 1
            else ""
        )
        state.fail(step_name, err + extra)
        return 1

    # 依原本 cycles 順序合併，輸出穩定（不受 as_completed 完成順序影響）
    for group, cycle_id in cycles:
        test_cases, results = cycle_outputs.get(group, ([], []))
        test_cases_by_cycle[group] = {
            "cycle_id": cycle_id,
            "test_cases": test_cases,
        }
        flat_test_cases.extend(test_cases)
        ai_results_by_cycle[group] = {
            "cycle_id": cycle_id,
            "results": results,
        }
        flat_ai_results.extend(results)

    state.set(
        test_cases_by_cycle=test_cases_by_cycle,
        test_cases=flat_test_cases,
        ai_results_by_cycle=ai_results_by_cycle,
        ai_results=flat_ai_results,
    )

    # ---- Step 6: fetch automation platform case list ----
    # 用來標記哪些 MUST/SHOULD case 是真的有自動化（可一鍵觸發 single test run）
    automated_case_ids = fetch_automated_case_ids(state)
    state.set(automated_case_ids=automated_case_ids)

    state.complete()
    return 0


if __name__ == "__main__":
    sys.exit(main())
