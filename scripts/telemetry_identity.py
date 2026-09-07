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

另外提供 `resolve_skills_version()`：本 clone 的 git short HEAD，用來在後台看「誰在跑哪一版」。
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


def resolve_skills_version() -> str:
    """本 clone 的 git short HEAD（例 "2245a4d"）；取不到回 ""。

    以「本檔所在的 clone」為目標，不用 cwd——hook 執行時的工作目錄是使用者當下的專案
    （多半是 kkday-QA-automation），用 cwd 會量到別的 repo 的版本。
    dirty（有未 commit 的改動）時加 "+"，才分得出「跟 master 一樣」與「本機自己改過」。
    """
    global _SKILLS_VERSION
    if _SKILLS_VERSION is not None:
        return _SKILLS_VERSION
    _SKILLS_VERSION = ""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        rev = (out.stdout or "").strip()
        if not rev:
            return _SKILLS_VERSION
        dirty = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=3,
        )
        _SKILLS_VERSION = rev + ("+" if (dirty.stdout or "").strip() else "")
    except Exception:
        pass
    return _SKILLS_VERSION


if __name__ == "__main__":
    print(f"operator={resolve_operator()}")
    print(f"client_user={resolve_client_user()}")
    print(f"skills_version={resolve_skills_version()}")
