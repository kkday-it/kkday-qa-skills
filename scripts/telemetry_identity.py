#!/usr/bin/env python3
"""
共用：解析遙測用的 operator / client_user 身分。

三支 sender（send_case_fidelity / send_tool_usage / send_locator_registry）共用這裡，
避免各自複製一份身分邏輯而漂移——它們的值同時餵 ai_studio「Case 忠實度分析」與
「MCP 呼叫分析」兩個 dashboard 的「誰在用」。

兩個欄位規則不同：
    operator     = $KKDAY_TOOLS_USER_NAME（MCP / X-User-Name 對應）
                 → $USER / $LOGNAME（Claude Code 這個 session 跑在誰底下）
                 → "kkday_qa_mcp"
                 **不回退 git。**
    client_user  = "<login>@<hostname>"，login **先用 os.getlogin()**；
                   getlogin() 拿不到（容器 / hook 環境）才退用 **git user.name / email**。

另外提供 `resolve_skills_version()`：本 clone 的版本標記（git short HEAD ＋ dirty / 遮蔽後綴），
用來在後台看「誰在跑哪一版」；量不到時回 `unknown:<原因>` 而不是空字串（見該函式）。
🔴 它回的是**磁碟上的版本**，不是「這個 session 正在生效的 hook 版本」——那兩件事會不一樣，
因為 Claude Code 在啟動時把 hook 清單讀成快照，之後 `settings.json` 再被改寫也不重讀。
快照版本要另外由 `sync_hooks.py` 寫進 hook 指令的 `--hooks-rev N`（見該檔 `HOOKS_REV`），
兩個值一起送才看得出「已 pull 到新版、但還在用舊快照」的人。

全部 fail-safe：任何錯誤都吞掉、回退，絕不讓遙測發送因為「取身分」而失敗。
"""
import os
import socket
import subprocess


def _git(field: str) -> str:
    try:
        out = subprocess.run(
            ["git", "config", "--get", f"user.{field}"],
            capture_output=True, text=True, timeout=2,
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def resolve_operator() -> str:
    env = os.getenv("KKDAY_TOOLS_USER_NAME")
    if env:
        return env
    return (os.getenv("USER") or os.getenv("LOGNAME") or "").strip() or "kkday_qa_mcp"


def resolve_client_user() -> str:
    # 先用 os.getlogin()；拿不到才退 git 名稱
    try:
        login = os.getlogin()
    except Exception:
        login = ""
    if not login:
        login = _git("name") or _git("email") or "unknown"
    return f"{login}@{_hostname()}"


_SKILLS_VERSION = None


def _shadow_suffix() -> str:
    """`!N` = 有 N 個 skill/agent 被本機的非 symlink 檔案遮蔽（清單由 link_assets.sh 寫）。

    為什麼要跟版本號送在同一欄：兩個人的 `skills_version` 一模一樣，跑起來卻可能不同——
    因為其中一個的 `~/.claude/skills/<name>` 是自己的真目錄，`link_assets.sh` 依設計
    不覆蓋（見該檔）。少了這個後綴，dashboard 上那兩列長得一樣，差異查不出來。
    """
    claude_dir = os.getenv("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )
    try:
        with open(
            os.path.join(claude_dir, "harness", "link_conflicts.txt"), encoding="utf-8"
        ) as f:
            n = sum(1 for line in f if line.strip())
        return f"!{n}" if n else ""
    except Exception:
        # 檔案不存在＝這台還沒跑過新版 link_assets.sh，跟「沒有衝突」不同，
        # 但也沒有依據說它有衝突——留空，等它跑過一次就會有值。
        return ""


def resolve_skills_version() -> str:
    """本 clone 的版本標記，例 `2245a4d` / `2245a4d+` / `2245a4d+!2`。

    以「本檔所在的 clone」為目標，不用 cwd——hook 執行時的工作目錄是使用者當下的專案
    （多半是 kkday-QA-automation），用 cwd 會量到別的 repo 的版本。

    後綴：
        `+`   dirty（有未 commit 的改動），分得出「跟 master 一樣」與「本機自己改過」
        `!N`  有 N 個 skill/agent 被本機檔案遮蔽（見 `_shadow_suffix`）

    ## 取不到版本時回 `unknown:<原因>`，不回空字串

    原本回 ""。實測 9/07 欄位上線後，`root@catherine.liu` 送出的 16 筆**全是空字串**——
    但「空」在 dashboard 上跟「舊 sender 根本沒送這欄」是同一個樣子，所以沒人發現她那台
    量不到版本，也無從知道是沒裝 git、不是 clone、還是逾時。三種原因的處理方式完全不同，
    值得分開講。這條路本身仍然 fail-safe：不管哪種原因都回得出字串，絕不讓遙測發送失敗。
    """
    global _SKILLS_VERSION
    if _SKILLS_VERSION is not None:
        return _SKILLS_VERSION
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        rev = (out.stdout or "").strip()
        if not rev:
            # rc!=0：不是 git clone（下載 zip、被 rsync 過來）或還沒有任何 commit。
            _SKILLS_VERSION = "unknown:not-a-repo" if out.returncode else "unknown:empty"
            return _SKILLS_VERSION
    except FileNotFoundError:
        _SKILLS_VERSION = "unknown:no-git"
        return _SKILLS_VERSION
    except subprocess.TimeoutExpired:
        _SKILLS_VERSION = "unknown:timeout"
        return _SKILLS_VERSION
    except Exception:
        _SKILLS_VERSION = "unknown:error"
        return _SKILLS_VERSION

    # rev 已經拿到了。以下兩個後綴是加分項，任何一個失敗都**不能**把 rev 一起丟掉
    # （改版前 dirty 那步一噴例外，整個函式就回 "" —— 明明版本量得到）。
    dirty = ""
    try:
        st = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=3,
        )
        dirty = "+" if (st.stdout or "").strip() else ""
    except Exception:
        pass
    _SKILLS_VERSION = rev + dirty + _shadow_suffix()
    return _SKILLS_VERSION


if __name__ == "__main__":
    print(f"operator={resolve_operator()}")
    print(f"client_user={resolve_client_user()}")
    print(f"skills_version={resolve_skills_version()}")
