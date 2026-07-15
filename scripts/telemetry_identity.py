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


if __name__ == "__main__":
    print(f"operator={resolve_operator()}")
    print(f"client_user={resolve_client_user()}")
