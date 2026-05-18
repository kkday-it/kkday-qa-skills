#!/usr/bin/env python3
"""
component_mapping.yaml generator

撈 GitHub repo 樹，產出 component_mapping.yaml 的 patterns 草稿。

用法：
  # 單一 repo
  python3 gen_component_mapping.py --repo kkday-it/kkday-b2c-web

  # 跑所有支援的 repo
  python3 gen_component_mapping.py --all

  # 指定 branch / tag
  python3 gen_component_mapping.py --repo kkday-it/kkday-ios-member --ref master

輸出：yaml 文字（含分組註解），直接貼進 component_mapping.yaml 即可。
依賴：gh CLI 已認證、PyYAML（pip install pyyaml）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


# 每個 repo 的 scan 規則
# - scan_dirs: 要掃描的目錄路徑（相對 repo root）
# - depth: 從 scan_dir 往下幾層當作一個 component 分組
# - global_keywords: 子目錄名含這些字串 → is_global=true
# - ignore_keywords: 子目錄含這些字串 → 整段跳過
REPO_RULES: dict[str, dict[str, Any]] = {
    "kkday-it/kkday-ios-member": {
        "scan_dirs": ["Solution/kkday-ios-member/kkday-ios-member/App"],
        "depth": 1,
        "global_keywords": [
            "common", "util", "manager", "service", "network", "configuration",
            "extension", "helper", "framework",
        ],
        "ignore_keywords": ["test", "tests", "mock", "storyboard"],
    },
    "kkday-it/kkday-android-member": {
        # app/src/main/java/com/kkday/member 是舊架構（共 25 個底層 module），
        # 多數業務邏輯已搬到 libs/feature/{xxx_page}/。libs/tool 收工具（abtesting/router/...），
        # libs 頂層收 UI/base/networking/payment/routing/transportation 等共用庫。
        "scan_dirs": [
            "app/src/main/java/com/kkday/member",
            "libs/feature",
            "libs/tool",
            "libs",
        ],
        "depth": 1,
        "global_keywords": ["common", "util", "core", "network", "base", "data"],
        # feature/tool 已單獨列為 scan_dirs，這裡 libs 掃到時要跳過避免重複
        "ignore_keywords": ["test", "feature", "tool"],
    },
    "kkday-it/kkday-b2c-web": {
        "scan_dirs": [
            "apps/main/pages",
            "apps/main/components/mobile",
            "apps/main/components/desktop",
            "apps/main/multiples/desktop/components",
            "apps/main/multiples/mobile/components",
            "apps/main/server/api/_nuxt",
            "apps/main/composables",
            "apps/main/plugins",
            "apps/main/helpers",
            "apps/main/scripts",
            "packages/modules",
            "packages/effects",
        ],
        "depth": 1,
        "global_keywords": [
            "log", "log2", "logger", "auth", "common", "core", "base",
            "config", "i18n", "plugin", "helper", "util", "tracking",
        ],
        "ignore_keywords": ["__tests__", ".stories", ".test.", "__test-utils__"],
    },
    "kkday-it/kkday-member-ci": {
        # .NET 結構：核心都在 src/KKday/B2CWeb 底下
        "scan_dirs": ["src/KKday/B2CWeb"],
        "depth": 1,
        "global_keywords": [
            "common", "util", "utils", "core", "framework", "helper",
            "constants", "exception", "valueobject", "transformer",
        ],
        "ignore_keywords": ["test", "tests", "mock"],
    },
    "kkday-it/kkday-mobile-member-ci": {
        # .NET 結構：核心都在 src/KKday/B2CWeb 底下
        "scan_dirs": ["src/KKday/B2CWeb"],
        "depth": 1,
        "global_keywords": [
            "common", "util", "utils", "core", "framework", "helper",
            "constants", "exception", "valueobject", "transformer",
        ],
        "ignore_keywords": ["test", "tests", "mock"],
    },
    "kkday-it/kkday-b2c-api": {
        # Laravel 結構：核心都在 app/ 底下（沒有 src/）
        "scan_dirs": ["app", "app/Http", "app/Services"],
        "depth": 1,
        "global_keywords": [
            "common", "util", "utils", "core", "config", "shared",
            "contracts", "facades", "providers", "traits", "exceptions",
            "helpers", "libraries", "builders", "factories", "enums",
        ],
        "ignore_keywords": ["test", "tests", "mock"],
    },
}


# 英文目錄名 → 中文業務 component 名
NAME_MAP: dict[str, str] = {
    # Pages 業務
    "checkout": "結帳頁",
    "product": "商品詳情頁",
    "productlist": "商品列表",
    "search": "搜尋頁",
    "member": "會員中心",
    "membermenu": "會員選單",
    "order": "訂單頁",
    "orders": "訂單頁",
    "cart": "購物車",
    "home": "首頁",
    "category": "分類頁",
    "destination": "目的地頁",
    "destinationexplore": "目的地探索",
    "payment": "付款",
    "booking": "訂購流程",
    "newproduct": "商品詳情頁",
    "promo": "活動行銷",
    "rewards": "點數獎勵",
    "redeem": "兌換",
    "coupon": "優惠券",
    "point": "點數",
    "thsr": "高鐵",
    "merchant": "商家",
    "store": "店家",
    "review": "評論",
    # 系統 / 跨業務
    "auth": "身份驗證",
    "login": "登入",
    "i18n": "i18n 多語系",
    "seo": "SEO",
    "log": "日誌系統",
    "log2": "日誌系統",
    "logger": "日誌系統",
    "tracking": "埋點追蹤",
    "analytics": "埋點追蹤",
    "recommend": "推薦",
    "schema-org": "SEO 結構化資料",
    "structureddata": "SEO 結構化資料",
    "explore": "探索 / 首頁",
    "campaign": "活動",
    "ttdlanding": "目的地頁",
    "ttd": "目的地頁",
    "filter": "篩選",
    "modals": "Modal 元件",
    "errors": "錯誤頁",
    "cookie-policy-setting": "Cookie 政策",
    "freshchat": "Freshchat 客服",
    "infomessage": "訊息元件",
    "photocarousel": "照片輪播",
    # 補上 b2c-web server/api/_nuxt 與雜項
    "abtest": "AB 測試",
    "affiliate": "Affiliate 聯盟",
    "b2c-svc": "B2C 服務 API",
    "member-svc": "會員服務 API",
    "php-request": "PHP Request 代理",
    "cpath": "CPath 路徑",
    "currency": "貨幣",
    "dcs": "DCS",
    "device": "裝置資訊",
    "events": "事件追蹤",
    "experiment": "AB 實驗",
    "healthcheck": "健康檢查",
    "hooks": "Hooks",
    "local": "本地服務",
    "loginmodal": "登入 Modal",
    "tracker": "Tracker 追蹤",
    "travelvouchers": "旅行禮券",
    "validator": "驗證器",
    "vertical": "Vertical 垂直業務",
    "whlbsetting": "WHLB 設定",
    "wish": "願望清單",
    "membermenu": "會員選單",
    "newproduct": "商品詳情頁",
    # .NET (member-ci) 結構
    "apps": "Apps 應用層",
    "constants": "常數定義",
    "exception": "Exception 例外",
    "valueobject": "ValueObject",
    "transformer": "Transformer 轉換器",
    "http": "HTTP 請求層",
    # Laravel (b2c-api) 結構
    "builders": "Builders 建構器",
    "collectors": "Collectors 收集器",
    "console": "Console 指令",
    "contracts": "Contracts 介面",
    "entities": "Entities 實體",
    "enums": "Enums 列舉",
    "exceptions": "Exceptions 例外",
    "facades": "Facades",
    "factories": "Factories 工廠",
    "libraries": "Libraries 函式庫",
    "models": "Models 資料模型",
    "notifications": "Notifications 通知",
    "providers": "Providers",
    "repositories": "Repositories",
    "traits": "Traits",
    # iOS-specific（含中文目錄名）
    "搜尋引擎 search": "搜尋頁",
    "訂購確認 booking": "訂購確認頁",
    "會員 member": "會員中心",
    "推薦 recommend": "推薦",
    "首頁 home": "首頁",
    # Android-specific（libs/feature, libs/tool, libs/）
    "chat": "聊天",
    "hotel": "飯店",
    "notification": "通知中心",
    "creditcard": "信用卡",
    "order_core": "訂單核心庫",
    "product_core": "商品核心庫",
    "search_core": "搜尋核心庫",
    "shared_core": "共用核心庫",
    "shared_ui": "共用 UI",
    "abtesting": "AB 實驗",
    "debugtool": "除錯工具",
    "env": "環境設定",
    "event_bus": "Event Bus",
    "lint": "Lint",
    "reductor": "Reductor",
    "router": "路由",
    "view_builder": "View Builder",
    "webjs": "WebJS",
    "designsystem": "設計系統",
    "app_resource": "應用資源",
    "routing": "路由",
    "transportation": "交通",
    "ui": "UI 元件庫",
    "model": "Models 資料模型",
    "networking": "網路層",
    # Layout / effects
    "layouts": "Layout",
    "member-terms": "會員條款",
    # 基礎設施 / global
    "common": "通用元件",
    "util": "工具函式",
    "utils": "工具函式",
    "core": "核心框架",
    "base": "基底類別",
    "shared": "共用模組",
    "helper": "輔助工具",
    "helpers": "輔助工具",
    "extension": "Extension 擴充",
    "extensions": "Extension 擴充",
    "manager": "Manager 管理",
    "managers": "Manager 管理",
    "service": "Service 服務",
    "services": "Service 服務",
    "network": "網路層",
    "framework": "框架層",
    "configuration": "全局配置",
    "config": "全局配置",
    "plugin": "插件系統",
    "plugins": "插件系統",
    "modules": "模組層",
    "effects": "Effects",
    "composables": "Composables",
    "scripts": "腳本",
    "data": "資料層",
}


# LLM 給的 component 名稱常見變體 → yaml component 名稱的 alias 對應
# render yaml 時自動把 aliases 寫入對應 pattern；backend `_component_matches`
# 會把 component + aliases 都當作匹配候選（並再做一次 normalize）。
# 觀察 release-impact pipeline log 後補進這個 dict，下次 regenerate 自動帶上。
ALIAS_MAP: dict[str, list[str]] = {
    # 搜尋相關
    "搜尋頁": ["搜尋模組", "搜尋紀錄", "搜尋功能"],
    "篩選": ["商品篩選", "篩選模組"],
    # 商品相關
    "商品詳情頁": ["商品詳情模組", "商品模組", "商品內容頁"],
    "訂購流程": ["商品訂購", "商品訂購（門票）", "訂購模組"],
    # 訂單相關
    "訂單管理 Order": ["訂單管理", "訂單模組"],
    "訂單頁": ["訂單頁面"],
    # 購物車
    "購物車": ["購物車模組"],
    # 會員相關
    "會員中心": [
        "會員設定",
        "會員帳號",
        "用戶與認證",
        "帳號註冊/登入",
        "登入驗證",
        "會員服務",
    ],
    # 分類頁
    "CategoryPage": ["分類結果頁模組", "分類頁面"],
    "CategorySearchResultPage": ["分類結果頁"],
    # 多語系
    "translation": ["多語系設定", "多語系", "i18n"],
    # 全局
    "全局配置": ["系統設定", "設定模組", "App 設定"],
    "通用元件": ["通用基礎設施", "共用元件"],
    "Loading": ["UI顯示控制", "Loading 顯示"],
    # WebView
    "WebJS": ["WebView整合模組", "WebView"],
    # Bottom Sheet
    "UI 元件庫": ["Bottom Sheet / Dialog 元件", "Dialog 元件"],
    # App 啟動 / application
    "application": ["App 開啟與導轉", "App 啟動"],
}


def fetch_tree(repo: str, ref: str = "HEAD") -> list[dict[str, Any]]:
    """用 gh CLI 撈 repo 的 git tree（recursive）"""
    cmd = ["gh", "api", f"repos/{repo}/git/trees/{ref}?recursive=1"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    except subprocess.CalledProcessError as e:
        print(f"# ERROR: gh api failed for {repo}@{ref}: {e.stderr}", file=sys.stderr)
        return []
    except subprocess.TimeoutExpired:
        print(f"# ERROR: gh api timeout for {repo}", file=sys.stderr)
        return []
    data = json.loads(r.stdout)
    return data.get("tree", [])


def platform_prefix(scan_dir: str) -> str:
    """
    從 scan_dir 推斷平台前綴。
    apps/main/components/mobile/...     → "Mobile "
    apps/main/multiples/mobile/...      → "Mobile "
    apps/main/components/desktop/...    → "Desktop "
    apps/main/multiples/desktop/...     → "Desktop "
    其他                                  → ""
    """
    s = scan_dir.lower()
    if "/mobile/" in s + "/" or s.endswith("/mobile"):
        return "Mobile "
    if "/desktop/" in s + "/" or s.endswith("/desktop"):
        return "Desktop "
    return ""


_SUFFIX_PATTERN = re.compile(r"_(page|list|module|feature|service|core|ui)$")


def normalize_component(
    subdir: str, scan_dir: str, global_keywords: list[str]
) -> tuple[str, bool]:
    """子目錄名 → (component 中文名, is_global)"""
    key = subdir.lower().strip()
    # 拿掉常見前綴 (_components / _hooks 等)
    clean = key.lstrip("_")
    # 砍尾綴 → 對 NAME_MAP base entry（android `libs/feature/order_page` → order → 訂單頁）
    stripped = _SUFFIX_PATTERN.sub("", clean)
    name = (
        NAME_MAP.get(clean)
        or NAME_MAP.get(stripped)
        or NAME_MAP.get(key)
        or subdir
    )
    is_global = any(kw in key for kw in global_keywords)
    # global 通用設施不加平台前綴（共用就是共用）
    if not is_global:
        name = platform_prefix(scan_dir) + name
    return name, is_global


def gen_patterns(repo: str, rules: dict[str, Any], ref: str) -> list[dict[str, Any]]:
    tree = fetch_tree(repo, ref)
    if not tree:
        return []

    # 只挑 tree 型（目錄），blob 是檔案要排除
    dir_paths: set[str] = {
        e["path"] for e in tree if e.get("type") == "tree" and e.get("path")
    }

    patterns: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for scan_dir in rules["scan_dirs"]:
        prefix = scan_dir + "/"
        subdirs: set[str] = set()
        for path in dir_paths:
            if not path.startswith(prefix):
                continue
            tail = path[len(prefix):]
            parts = tail.split("/")
            if len(parts) < rules["depth"]:
                continue
            subdir = "/".join(parts[: rules["depth"]])
            # 忽略 ignore_keywords
            if any(kw.lower() in subdir.lower() for kw in rules.get("ignore_keywords", [])):
                continue
            if not subdir or subdir.startswith("."):
                continue
            # 排除明顯是檔名的（含副檔名）
            last = subdir.split("/")[-1]
            if "." in last and not last.startswith("_"):
                continue
            subdirs.add(subdir)

        # 對應到實際存在的目錄（避免假目錄）
        for subdir in sorted(subdirs):
            full = f"{scan_dir}/{subdir}"
            if full not in dir_paths:
                continue
            name, is_global = normalize_component(
                subdir, scan_dir, rules.get("global_keywords", [])
            )
            pattern = f"{full}/**"
            key = (name, pattern)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            patterns.append({
                "component": name,
                "is_global": is_global,
                "pattern": pattern,
            })

    return patterns


def render_yaml(grouped: dict[str, list[dict[str, Any]]]) -> str:
    """render yaml with comments grouping by repo"""
    lines = ["patterns:"]
    for repo, patterns in grouped.items():
        lines.append("")
        lines.append(f"  # ===== {repo} ({len(patterns)} patterns) =====")
        for p in patterns:
            lines.append(f"  - component: {p['component']}")
            lines.append(f"    is_global: {str(p['is_global']).lower()}")
            lines.append(f"    pattern: {p['pattern']}")
            aliases = ALIAS_MAP.get(p["component"])
            if aliases:
                lines.append(f"    aliases: {json.dumps(aliases, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="owner/name; 不給時用 --all")
    parser.add_argument("--all", action="store_true", help="跑所有支援的 repo")
    parser.add_argument("--ref", default="HEAD", help="branch / tag / SHA，預設 HEAD")
    parser.add_argument("--out", default="-", help="輸出檔（- 表示 stdout）")
    args = parser.parse_args()

    if args.all:
        targets = list(REPO_RULES.keys())
    elif args.repo:
        targets = [args.repo]
    else:
        parser.error("必須給 --repo 或 --all")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for repo in targets:
        rules = REPO_RULES.get(repo)
        if not rules:
            print(f"# WARN: no rules for {repo}, skipped", file=sys.stderr)
            continue
        print(f"[{repo}] fetching tree @ {args.ref}...", file=sys.stderr)
        patterns = gen_patterns(repo, rules, args.ref)
        print(f"[{repo}] {len(patterns)} patterns", file=sys.stderr)
        grouped[repo] = patterns

    output = render_yaml(grouped)
    if args.out == "-":
        print(output)
    else:
        from pathlib import Path
        Path(args.out).write_text(output)
        print(f"\nwrote to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
