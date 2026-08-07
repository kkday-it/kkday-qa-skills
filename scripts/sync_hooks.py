#!/usr/bin/env python3
"""
sync_hooks —— kkday-qa-skills hook 定義的【單一來源】，冪等 merge 進 user-level settings。

為什麼獨立成一支：install.sh 一次性安裝、session_autopull.sh 每次 pull 有更新時也會呼叫它，
兩邊共用同一份定義，避免「install 裝的」跟「autopull 該同步的」漂移。因為 hook 是用「本 clone
的絕對路徑」寫進 ~/.claude/settings.json 的快照，光 git pull **不會**更新這些指令字串——所以
autopull 拉到新版後要再跑這支，team 才會在下次 pull 自動拿到改過的 hook（flag / 路徑）。

**自動 migrate**：屬於本 repo（command 含 `<repo>/scripts/`）但已不在目前定義裡的舊 hook 會被
移除、換成現行版本；非本 repo 的 hook 一律不動。整支 fail-safe：任何錯誤吞掉、exit 0，不擋 session。

用法：
    python3 sync_hooks.py                 # 用本檔所在 clone 的路徑
    REPO=/path/to/clone python3 sync_hooks.py
    SETTINGS=/path/to/settings.json python3 sync_hooks.py   # 覆寫目標（測試用）
"""
import json
import os
import sys


def _repo() -> str:
    return os.environ.get("REPO") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )


def _settings_path() -> str:
    if os.environ.get("SETTINGS"):
        return os.environ["SETTINGS"]
    claude_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )
    return os.path.join(claude_dir, "settings.json")


def desired_hooks(repo: str) -> dict:
    """event -> list of entry（本 repo 目前該有的 hook）。

    entry 可以是 command 字串（不限定 tool，適用 SessionStart / Stop 這類無 matcher 的
    event），或 `{"matcher": "...", "command": "..."}`（PreToolUse / PostToolUse 需要）。
    """
    return {
        "SessionStart": [
            f'bash "{repo}/scripts/session_autopull.sh"',
        ],
        "UserPromptSubmit": [
            f'bash "{repo}/scripts/session_autopull.sh" 1800',
            # 路由層：「KQT-T1234 實作」→ 注入 planner → automator → reviewer 的具體指示。
            # 是 best-effort（沒有 hook 能強迫模型呼叫 Agent），漏掉的由下面 PreToolUse 接住。
            f'python3 "{repo}/scripts/case_impl_router.py"',
        ],
        # 攔截層：真的要寫實作檔時反問人——這是 case 實作（該走 qa-case-automator）還是
        # 小修（typo / merge conflict / lint）？matcher 一定要有，否則每個 tool call 都會
        # 起一次 python，白付延遲。
        "PreToolUse": [
            {
                "matcher": "Edit|Write|NotebookEdit",
                "command": f'python3 "{repo}/scripts/agent_only_impl_guard.py"',
            },
        ],
        # 順序有意義：send_case_fidelity 必須在 gate **之前**——先把遙測送出，gate 才在 pass 時
        # 刪掉結果目錄。反過來（gate 先跑並在 pass 刪檔）會讓通過那輪的遙測還沒送就被刪。
        # send_case_fidelity **不帶 --purge**：結果目錄的生命週期交給 gate（pass 才刪），否則
        # gate 擋下時被 sender 刪掉輸入 → 下輪假性「找不到結果」卡死。
        # 順序有意義（見各 gate 說明）：sender 先送、對應 gate 隨後把關並在 pass 時清生命週期。
        # send_case_fidelity / send_locator_registry 都**不帶 --purge**：結果/emit 檔是各自 gate 的
        # 證據，生命週期交給 gate（pass 才清），否則 sender 在 gate 擋下時先刪掉證據 → 假性卡死。
        # locator 後端是 upsert（冪等），不 purge 期間重送無害。
        "Stop": [
            f'python3 "{repo}/scripts/send_case_fidelity.py" --indir /tmp/case_fidelity_results.d',
            f'bash "{repo}/scripts/fidelity_gate_stop_hook.sh"',
            f'python3 "{repo}/scripts/send_locator_registry.py" --indir /tmp/locator_results.d',
            f'bash "{repo}/scripts/locator_gate_stop_hook.sh"',
            f'python3 "{repo}/scripts/send_tool_usage.py" --infile /tmp/tool_usage.jsonl --purge',
        ],
    }


def sync(cfg: dict, repo: str) -> dict:
    """把 desired hook merge 進 cfg（就地改並回傳）。

    策略：對每個 event，先移除**屬本 repo 的所有** hook（command 含 `<repo>/scripts/`），
    再以 desired 的順序整組重加。這樣同時保證：(1) 自動 migrate 改過 flag/路徑的舊 hook，
    (2) 本 repo hook 的**相對順序**永遠等於 desired（順序對 Stop hook 的正確性有意義），
    (3) 冪等（重跑結果相同）。**非本 repo 的 hook 一律不動。**

    重加時依 matcher 分組：**相鄰**且 matcher 相同的 entry 併成同一個 group，matcher 一變
    就開新 group。用相鄰而非全域分組，是為了不打亂 desired 的順序。
    """
    hooks = cfg.setdefault("hooks", {})
    ours_marker = f"{repo}/scripts/"
    for event, entries in desired_hooks(repo).items():
        arr = hooks.setdefault(event, [])
        # 1) 移除本 repo 的所有 hook（稍後以 desired 順序重建）
        for grp in arr:
            grp["hooks"] = [
                h for h in grp.get("hooks", [])
                if ours_marker not in (h.get("command") or "")
            ]
        arr[:] = [grp for grp in arr if grp.get("hooks")]  # 丟掉被清空的 group
        # 2) 以 desired 順序重加。先建在獨立 list 再接到尾端——絕不把我們的 hook
        #    塞進殘留的外部 group（那會連帶改到別人 hook 的 matcher 語意）。
        ours: list = []
        for entry in entries:
            if isinstance(entry, str):
                matcher, command = None, entry
            else:
                matcher, command = entry.get("matcher"), entry["command"]
            hook = {"type": "command", "command": command}
            if ours and ours[-1].get("matcher") == matcher:
                ours[-1]["hooks"].append(hook)
            else:
                # matcher 排在 hooks 前面，跟 settings.json 既有寫法一致（人會讀這個檔）
                grp = {"matcher": matcher} if matcher is not None else {}
                grp["hooks"] = [hook]
                ours.append(grp)
        arr.extend(ours)
    return cfg


def main() -> int:
    try:
        repo = _repo()
        path = _settings_path()
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        sync(cfg, repo)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if sys.stdout.isatty():
            print(f"[sync_hooks] hooks synced into {path}")
    except Exception:
        pass  # fail-safe：絕不擋 session / install
    return 0


if __name__ == "__main__":
    sys.exit(main())
