"""從 Kibana 反推 APP 打的 API contract。

APP（iOS/Android 原生）沒有 DevTools、又有 SSL pinning，抓不到封包，Kibana 是唯一的來源。

用法（env / device / 帳號 / 裝置識別全部預設自動推，不用自己填）:
  python app_api_from_kibana.py --platform ios                     # 這台實機剛打了哪些 endpoint
  python app_api_from_kibana.py --platform ios --email auto        # 再收斂到該平台預設測試帳號
  python app_api_from_kibana.py --platform ios --route api/v2/dcs --detail   # 單支完整 contract
  python app_api_from_kibana.py --platform ios --list-devices      # 該時段實際有哪些裝置在打

⚠️ --env 必須是那台裝置實際打的環境。撈錯 env 不會報錯：不是回 0 筆，就是回同時段
   別人的流量（實測 sit 有一堆 Simulator 的 log）——兩者都跟「這段沒操作」長得一模一樣。
   所以預設 auto：拿裝置指紋去各環境試撈，哪邊有資料算哪邊。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

def _is_framework(p):
    return os.path.isfile(os.path.join(p, "QATest", "src", "qatest", "__init__.py"))


def _resolve_framework():
    """1) QA_FRAMEWORK_PATH  2) cwd 往上找。找不到回 None —— framework 是選配。

    這支唯一非得靠 framework 的功能是 `--email auto`（要它的帳號設定檔）。撈 log 本身
    走同層的 `kibana_client.py`，所以沒有 clone 的人也用得起來。
    """
    env_path = os.environ.get("QA_FRAMEWORK_PATH")
    if env_path:
        p = os.path.abspath(os.path.expanduser(env_path))
        if not _is_framework(p):
            sys.exit(f"QA_FRAMEWORK_PATH 不是 framework repo: {p}")
        return p

    cur = os.getcwd()
    while True:
        if _is_framework(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from kibana_client import KibanaClient, resolve_kibana_url  # noqa: E402  同層，獨立版（只需要 requests）
except ImportError as e:
    sys.exit(f"無法 import kibana_client（{e}）。它應該跟這支放在同一層。缺的話：pip install requests")

import device_registry  # noqa: E402  ai_studio 裝置註冊庫（udid → ad-id）

_FW = _resolve_framework()
if _FW:
    sys.path.insert(0, os.path.join(_FW, "QATest", "src"))

PLATFORM_FIELD = "custom_api-b2c.platform"
MEMBER_FIELD = "custom_api-b2c.member_uuid"
SOURCE_FIELD = "custom_api-b2c.source"
SECRET = re.compile(r'(?i)(authorization|token\d?|password|secret|cookie)')

# ⚠️ log 裡大小寫不一致（實測 sit：iOS 3039 / IOS 642 / ANDROID 1974 / Android 4）。
# 只比對單一拼法會靜默漏掉一半流量，所以每個平台都要列出全部變體。
SOURCE_VARIANTS = {
    "ios": ["iOS", "IOS", "ios"],
    "android": ["ANDROID", "Android", "android"],
}


# header 的 device-model 是行銷名，ideviceinfo 只給得出 ProductType，所以 iOS 要轉一次。
# 對照表不自己維護——抓社群在維護的那份，存本地快取；抓不到才用內建的少數幾筆頂著。
IOS_MAP_URL = ("https://raw.githubusercontent.com/kyle-seongwoo-jun/"
               "apple-device-identifiers/main/ios-device-identifiers.json")
IOS_MAP_CACHE = os.path.expanduser("~/.cache/apple-device-identifiers.json")
IOS_PRODUCT_TYPE_FALLBACK = {
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
}


def ios_product_type_map():
    try:
        import urllib.request
        with urllib.request.urlopen(IOS_MAP_URL, timeout=10) as r:
            data = json.loads(r.read().decode())
        os.makedirs(os.path.dirname(IOS_MAP_CACHE), exist_ok=True)
        with open(IOS_MAP_CACHE, "w") as f:
            json.dump(data, f)
        return data
    except Exception:
        pass
    try:
        with open(IOS_MAP_CACHE) as f:
            return json.load(f)
    except Exception:
        return IOS_PRODUCT_TYPE_FALLBACK


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def detect_device(platform):
    """從連著的實機自動抓 (device-model, udid)。抓不到回 (None, None)。

    udid / serial 是**裝置端唯一拿得到**的穩定識別，拿它當裝置註冊庫的 key（見 device_registry）。
    device-model 只是 header 上那個行銷名，不唯一。
    """
    if platform == "android":
        lines = _run(["adb", "devices"]).splitlines()[1:]
        serials = [l.split()[0] for l in lines if l.strip().endswith("device")]
        if not serials:
            return None, None
        model = _run(["adb", "-s", serials[0], "shell", "getprop", "ro.product.model"])
        return (model or None), serials[0]
    udids = [u for u in _run(["idevice_id", "-l"]).splitlines() if u.strip()]
    if not udids:
        return None, None
    pt = _run(["ideviceinfo", "-u", udids[0], "-k", "ProductType"])
    name = ios_product_type_map().get(pt)
    if not name and pt:
        print(f"(不認得 ProductType {pt!r}——用 --list-devices 看該時段真實的 device-model，"
              f"再 --device '<值>'）")
    return name, udids[0]


ACCOUNT_KEY = {"ios": ("app", "ios_login_id"), "android": ("app", "android_login_id")}


def default_email(platform, env):
    """照 framework 既有做法拿該平台的預設測試帳號（跟 login_with_email_account 同一把 key）。"""
    service, key = ACCOUNT_KEY.get(platform, ACCOUNT_KEY["ios"])
    if not _FW:
        print("(--email auto 需要 framework clone：設 QA_FRAMEWORK_PATH，或改用 --email <addr>)")
        return None
    try:
        from lib import util
        account, _ = util.get_account_and_password(env, service, key)
        return account
    except Exception as e:
        print(f"(取預設帳號失敗：{type(e).__name__}: {e}——要指定請用 --email <addr>）")
        return None


def email_to_member_uuid(client, email, rng):
    """從 log 反查 member_uuid，不需要 DB 也不需要 MCP。

    登入前後的請求/回應 body 會同時出現 email 與 member_uuid，交集就是答案。
    """
    for field in ("response.body", "request.body"):
        q = {"size": 0, "query": {"bool": {"must": [
                {"range": {"@timestamp": rng}},
                {"match_phrase": {field: email}},
                {"exists": {"field": MEMBER_FIELD}}]}},
             "aggs": {"m": {"terms": {"field": f"{MEMBER_FIELD}.keyword", "size": 5}}}}
        try:
            rv = client.search(q)
        except Exception:
            continue
        buckets = [b for b in (((rv.get("aggregations") or {}).get("m") or {}).get("buckets") or [])
                   if b["key"]]
        if buckets:
            return max(buckets, key=lambda b: b["doc_count"])["key"]
    return None


def count_docs(client, must):
    rv = client.search({"size": 0, "query": {"bool": {"must": must}}})
    total = (rv.get("hits") or {}).get("total")
    return total.get("value") if isinstance(total, dict) else (total or 0)


def is_prod(env):
    """這個 env 是不是指向 prod 那座 Kibana。

    比對解析後的 URL 而不是字串本身：`prod` / `production` 會落到同一座，之後多一個別名也自動算。
    """
    return bool(env) and resolve_kibana_url(env) == resolve_kibana_url("prod")


PROD_REFUSAL = """
🚨 這會撈 prod（{url}）—— 停下來先問人。

prod 的 log 是**真實客戶的資料**：headers 裡有 member-uuid、token、裝置識別、IP，
body 裡有姓名、email、電話、訂單、金流。sit / stage 撈錯頂多白忙一場，prod 撈錯是把
客戶個資拉出來放進終端機、對話紀錄與截圖裡，收不回去。而且同一座 Kibana 是線上監控
在用的，寬時間窗的查詢會影響正在處理事故的人。

要繼續：
  1. 先跟使用者講明你要撈 prod 的什麼、為什麼 sit / stage 不夠，**等他明確同意**。
  2. 同意後加 --allow-prod 重跑，並且：
       - 時間窗壓到分鐘級（不是 --minutes 60，是你真正要看的那幾分鐘）
       - 能用統計就不要拉 hits，不要沒事就 --detail
       - 別把撈到的 headers / body 原文貼進報告或 PR

（沒得到同意就不要加那個 flag。這道關卡就是為了讓人先看見。）
""".strip()


def resolve_env(args):
    """--env auto：拿裝置指紋去各環境試撈，哪個有資料就是哪個。

    撈錯 env 不會報錯（回 0 筆或回別人的流量），是這支腳本最貴的坑，所以預設不讓人選。
    沒有裝置指紋時無從分辨——兩個環境都有 APP 流量——只能回退預設值並講明。
    """
    if args.env != "auto":
        return args.env
    if not args.device:
        print("(--env auto 需要裝置指紋才分得出環境，先退回 sit；要指定請用 --env stage）")
        return "sit"
    counts = {}
    for env in ("stage", "sit"):
        try:
            client = KibanaClient.for_env(env)
            must = base_filters(args)
            # 這裡也要精準比：ES 那層的 device-model 是會誤中同前綴型號的預篩（見 fetch_docs）。
            # 拿預篩數字來選 env，等於讓「別人那台 iPhone 15 Plus 在哪個環境比較忙」決定我們撈哪邊。
            counts[env] = (count_docs(client, must) if args.ad_id
                           else len(fetch_docs(client, must, "device-model", args.device)))
        except Exception as e:
            counts[env] = f"ERR {e}"
    hit = [(n, e) for e, n in counts.items() if isinstance(n, int) and n > 0]
    summary = "  ".join(f"{e}={n}" for e, n in counts.items())
    if not hit and args.ad_id:
        # 註冊庫那筆 ad-id 各環境都沒流量：可能是 App 重裝換了 IDFV。退回型號重選一次，
        # 讓後面的 resolve_identity 去判定並標 stale——不能就這樣認定「沒操作」。
        print(f"(註冊庫的 ad-id 各環境都沒流量：{summary}，退回用型號分辨環境)")
        args.ad_id = None
        return resolve_env(args)
    if not hit:
        print(f"(--env auto 各環境都沒有 {args.device!r} 的流量：{summary}，先退回 sit）")
        return "sit"
    env = max(hit)[1]
    print(f"(--env auto 選 {env}：{summary})")
    return env


DEVICE_DOC_CAP = 3000


def fetch_docs(client, must, header_key, header_value):
    """撈 REQUEST 並在 Python 端比 header **完全相等**。

    `request.headers` 是全文欄位，ES 的 match_phrase `iPhone 15` 會把 `iPhone 15 Plus`
    一起比中（實測混進 6 筆別台的），keyword 子欄位對這種長字串又不可靠。所以 ES 那層
    只當便宜的預篩，真正的判準在這裡。
    """
    rv = client.search({"size": DEVICE_DOC_CAP, "sort": [{"@timestamp": "desc"}],
                        "query": {"bool": {"must": must}}})
    hits = (rv.get("hits") or {}).get("hits") or []
    total = (rv.get("hits") or {}).get("total")
    total = total.get("value") if isinstance(total, dict) else (total or 0)
    if total > len(hits):
        print(f"  (預篩 {total} 筆超過單次上限 {DEVICE_DOC_CAP}，只取最近 {len(hits)} 筆——請縮小時間窗)")
    out = []
    for h in hits:
        src = h.get("_source") or {}
        hdr = parse_headers((src.get("request") or {}).get("headers"))
        if hdr.get(header_key) == header_value:
            out.append((src, hdr))
    return out


def scan_ad_ids(client, args):
    """該時段這個型號的 REQUEST 裡出現過哪些 ad-id，各自搭過哪些 member_uuid。

    刻意拿掉 route / 帳號 / ad-id 三個過濾——這一趟是在「找出自己是誰」，先收斂反而會把答案濾掉。
    """
    probe = dict(vars(args))
    probe.update({"ad_id": None, "member_uuid": None, "route": None})
    seen = {}
    for src, hdr in fetch_docs(client, base_filters(argparse.Namespace(**probe)),
                              "device-model", args.device):
        ad = hdr.get("ad-id")
        if not ad:
            continue
        info = seen.setdefault(ad, {"uuids": set(), "n": 0, "ver": "", "mixpanel": ""})
        info["uuids"].add((src.get("custom_api-b2c") or {}).get("member_uuid") or "")
        info["n"] += 1
        info["ver"] = info["ver"] or hdr.get("x-req-version") or ""
        info["mixpanel"] = info["mixpanel"] or hdr.get("mixpanel-id") or ""
    return seen


def pick_ad_id(seen, member_uuid):
    """從候選裡挑出「就是這台」。挑不出唯一解回 None——寧可不收斂，也不要收斂到別台。

    device-model 不夠精準：① 全文比對會把 `iPhone 15 Plus` 算進 `iPhone 15` ② 就算精確比對，
    同型號可能好幾台在打。帳號也不夠——實測同一個 kkqa_auto 帳號同時掛在兩台上（iPhone 15 Plus
    92 筆、我們這台 5 筆）。**兩者取交集才唯一。**
    """
    if not seen:
        return None
    cands = seen
    if len(cands) > 1 and member_uuid:
        narrowed = {a: i for a, i in cands.items() if member_uuid in i["uuids"]}
        if narrowed:
            cands = narrowed
    if len(cands) > 1:
        print(f"  ⚠️ 同型號有多台在打，無法自動判定是哪台：{sorted(cands)}——用 --ad-id 指定")
        return None
    return next(iter(cands))


def resolve_identity(client, args, cached=None):
    """收斂到「就是這台」的唯一識別 ad-id：先問裝置註冊庫，問不到才從 log 推，推出來寄回去。

    每個 REQUEST header 都有 ad-id，拿它當識別能一併蓋到不帶 member_uuid 的商品／搜尋 endpoint
    （member_uuid 只出現在會員域：實測 42 筆 trace 只有 12 筆帶得出來）。

    ad-id 裝置端讀不到（IDFA/IDFV 類，只有 app 行程內拿得到；ideviceinfo / adb / idevicesyslog
    都不吐，實測 126,287 行 syslog 零命中），只能這樣反推。但同一個安裝固定不變，所以推一次就
    寄回 ai_studio 共用，用裝置端拿得到的 udid 當 key。

    🔴 **用前先驗**：app 重裝會換 IDFV。判準不是「撈不到」——那也可能只是這段沒操作——而是
    「同型號有流量、裡面卻沒有這個 ad-id」。沒流量時不下判斷，直接沿用。
    """
    if args.ad_id and args.ad_id != cached:
        return args.ad_id  # --ad-id 是人明講的，不去驗也不覆寫
    if not args.device:
        return args.ad_id
    plat = args.platform or "ios"
    seen = scan_ad_ids(client, args)

    if cached:
        if not seen:
            print(f"  (沿用註冊庫的 ad-id；這時間窗內 {args.device!r} 沒流量，不判定新舊)")
            return cached
        if cached in seen:
            return cached
        print("  (註冊庫那筆 ad-id 沒出現在這段流量、同型號卻有——多半是 App 重裝換了 "
              "IDFV，標 stale 重推)")
        device_registry.save(args.udid, plat, status="stale")

    ad = pick_ad_id(seen, args.member_uuid)
    if ad and args.udid:
        info = seen[ad]
        device_registry.save(
            args.udid, plat, ad_id=ad, device_model=args.device,
            mixpanel_id=info["mixpanel"], app_version=info["ver"],
            derived_from=f"device-model ∩ member_uuid @{args.env} ({info['n']} docs)")
    return ad


def route_counts(client, must, args, top=50):
    """回 ([(route, n)…], total docs)。

    ad-id 是 UUID，全文比對就等同精確，交給 ES 的 terms aggregation 算就準。只有 device-model
    收斂時不行——那層是會誤中同前綴型號的預篩（見 fetch_docs），agg 出來的數字混了別台的，
    所以改成逐筆比完再數。
    """
    if args.device and not args.ad_id:
        counts = {}
        for src, _ in fetch_docs(client, must, "device-model", args.device):
            route = (src.get("request") or {}).get("route")
            if route:
                counts[route] = counts.get(route, 0) + 1
        rows = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
        return rows, sum(counts.values())

    rv = client.search({"size": 0, "query": {"bool": {"must": must}},
                        "aggs": {"routes": {"terms": {"field": "request.route.keyword",
                                                      "size": top}}}})
    total = (rv.get("hits") or {}).get("total")
    total = total.get("value") if isinstance(total, dict) else (total or 0)
    buckets = ((rv.get("aggregations") or {}).get("routes") or {}).get("buckets") or []
    return [(b["key"], b["doc_count"]) for b in buckets], total


def list_devices(client, must, index_size=300):
    """把該時段實際出現過的裝置指紋列出來。

    device-model 埋在 headers 字串裡、不能做 aggregation，所以用抽樣 + 解析。
    撈不到自己那台時，這份清單能直接分辨是「env 撈錯」還是「型號字串對不上」。
    """
    rv = client.search({"size": index_size, "sort": [{"@timestamp": "desc"}],
                        "query": {"bool": {"must": must + [{"term": {"log_label.keyword": "REQUEST"}}]}}})
    seen = {}
    for h in (rv.get("hits") or {}).get("hits") or []:
        req = ((h.get("_source") or {}).get("request") or {})
        hdr = parse_headers(req.get("headers"))
        model = hdr.get("device-model")
        if not model:
            continue
        key = (model, hdr.get("locale"), hdr.get("x-req-source"), hdr.get("x-req-version"))
        seen[key] = seen.get(key, 0) + 1
    print("  該時段出現過的裝置（device-model / locale / source / app 版本）:")
    if not seen:
        print("    (抽樣中沒有任何帶 header 的 REQUEST——多半是 env 撈錯)")
    for (model, locale, src, ver), n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>4}  {model!r}  locale={locale!r}  {src}  v{ver}")


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if SECRET.search(k) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def parse_headers(raw):
    """headers 是 [{k:v},{k:v}] 形式的 JSON string。"""
    if not raw:
        return {}
    try:
        arr = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"_raw": str(raw)[:200]}
    out = {}
    for item in arr if isinstance(arr, list) else []:
        if isinstance(item, dict):
            out.update(item)
    return out


def parse_time(s):
    """把使用者給的時間轉成 ES 能吃的值。

    ⚠️ log 的 @timestamp 是 UTC，人看的是本地時間（台北 UTC+8）。直接把 "14:30" 當 UTC 送會
    差 8 小時、撈回空的——而「空的」跟「那段真的沒流量」長得一模一樣。所以一律當本地時間解析，
    轉成帶 offset 的 ISO 字串讓 ES 自己換算。

    支援：now-2h / now（date math 原樣送）、HH:MM（今天）、YYYY-MM-DD HH:MM[:SS]、完整 ISO。
    """
    s = s.strip()
    if s.startswith("now"):
        return s

    local_tz = datetime.datetime.now().astimezone().tzinfo
    fmts = ["%H:%M", "%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%m-%d %H:%M"]
    for f in fmts:
        try:
            dt = datetime.datetime.strptime(s, f)
        except ValueError:
            continue
        today = datetime.datetime.now()
        if "%Y" not in f:
            dt = dt.replace(year=today.year,
                            month=dt.month if "%m" in f else today.month,
                            day=dt.day if "%d" in f else today.day)
        return dt.replace(tzinfo=local_tz).isoformat()

    try:  # 已帶時區的完整 ISO
        return datetime.datetime.fromisoformat(s).isoformat()
    except ValueError:
        sys.exit(f"看不懂的時間格式: {s!r}（可用 now-2h / 14:30 / '2026-09-14 14:30'）")


def time_range(args):
    if args.from_time or args.to_time:
        return {"gte": parse_time(args.from_time or "now-24h"),
                "lte": parse_time(args.to_time or "now")}
    return {"gte": f"now-{args.minutes}m", "lte": "now"}


def meta_status(resp):
    """回應 body 裡的業務狀態（`0000` 才是成功）。HTTP 200 配 M001 這種事很常見。"""
    try:
        m = (json.loads(resp.get("body") or "{}").get("metadata") or {})
    except Exception:
        return ""
    return " ".join(str(m.get(k)) for k in ("status", "desc") if m.get(k))


def fetch_response(client, args, uuid, route):
    """撈這支請求自己的回應。

    ⚠️ `request.uuid` 是整條 trace 的 id，不是單一請求的：同一個 uuid 底下還有 svc-member /
    api-product / payment …十幾筆下游的 REQUEST/RESPONSE。只用 uuid 撈會拿到某個下游服務的
    回應，而它長得完全像是這支 API 回的（實測拿到 member info，差點就當成付款 API 的回應）。
    所以一定要同時鎖 route 與 log_label。

    時間窗往後放寬 5 分鐘：回應通常只差幾毫秒，但請求落在窗尾那一刻時回應會掉到窗外。
    """
    if not uuid or not route:
        return None
    rng = dict(time_range(args))
    try:
        end = datetime.datetime.fromisoformat(rng["lte"]) + datetime.timedelta(minutes=5)
        rng["lte"] = end.isoformat()
    except Exception:
        pass
    rv = client.search({
        "size": 1, "sort": [{"@timestamp": "asc"}],
        "query": {"bool": {"must": [
            {"range": {"@timestamp": rng}},
            {"match_phrase": {"request.uuid": uuid}},
            {"term": {"request.route.keyword": route}},
            {"term": {"log_label.keyword": "RESPONSE"}}]}}})
    hits = (rv.get("hits") or {}).get("hits") or []
    return (hits[0].get("_source") or {}).get("response") if hits else None


def base_filters(args):
    must = [{"range": {"@timestamp": time_range(args)}}]
    if args.member_uuid:
        must.append({"term": {f"{MEMBER_FIELD}.keyword": args.member_uuid}})
    else:
        must.append({"term": {f"{PLATFORM_FIELD}.keyword": "APP"}})
    if args.route:
        must.append({"match_phrase": {"request.route": args.route}})
    if args.platform:
        must.append({"terms": {f"{SOURCE_FIELD}.keyword": SOURCE_VARIANTS[args.platform]}})
    # headers 是字串，只能全文比對；RESPONSE 沒有 headers，所以這兩個過濾只留得住 REQUEST。
    # ad-id 是 UUID，全文比對就等同精確；device-model 會誤中同前綴型號，只當預篩（見 fetch_docs）。
    if args.ad_id:
        must.append({"match_phrase": {"request.headers": args.ad_id}})
    elif args.device:
        must.append({"match_phrase": {"request.headers": args.device}})
    return must


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="auto",
                    help="sit / stage；預設 auto 會拿裝置指紋去各環境試撈，哪邊有資料算哪邊")
    ap.add_argument("--allow-prod", dest="allow_prod", action="store_true",
                    help="確認要撈 prod（真實客戶資料）。**必須先得到使用者明確同意**才加這個 flag")
    # 實作時的典型用法：操作完馬上撈，15 分鐘window 夠用。
    ap.add_argument("--minutes", type=int, default=15,
                    help="相對時間窗（分鐘），預設 15")
    ap.add_argument("--from", dest="from_time", metavar="T",
                    help="起始時間（本地時區）: 'now-2h' / '14:30' / '2026-09-14 14:30'")
    ap.add_argument("--to", dest="to_time", metavar="T",
                    help="結束時間（本地時區），預設 now")
    ap.add_argument("--member-uuid",
                    help="直接給 uuid。注意：只有會員域 endpoint 才帶 member_uuid，"
                         "商品/搜尋那些不帶，單靠它會漏掉大半 trace")
    ap.add_argument("--email", metavar="ADDR",
                    help="用 email 反查 member_uuid；給 auto 就取該平台的 framework 預設帳號")
    ap.add_argument("--device", metavar="STR", default="auto",
                    help="用 header 指紋收斂到自己那台，例如 'iPhone 15'（只會留下 REQUEST）。"
                         "預設 auto 從連著的實機自動抓；不要收斂就給 none")
    ap.add_argument("--ad-id", dest="ad_id", metavar="UUID",
                    help="直接指定裝置識別（同型號多台時的手動入口）。預設會用 udid 去 ai_studio "
                         "裝置註冊庫查，查不到再從 log 推、推出來存回去")
    ap.add_argument("--list-devices", action="store_true",
                    help="列出該時段實際出現過的裝置指紋，不做其他查詢")
    ap.add_argument("--route")
    ap.add_argument("--platform", choices=sorted(SOURCE_VARIANTS))
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args()

    args.udid = None
    if args.device == "none":
        args.device = None
    elif args.device == "auto":
        args.device, args.udid = detect_device(args.platform or "ios")
        if args.device:
            print(f"(--device auto 偵測到 device-model={args.device!r})")
        else:
            print("(沒偵測到連線實機，不做裝置收斂；要指定用 --device '<device-model>'）")

    # 先問註冊庫：有登記過就直接拿 ad-id 去分辨環境。用型號分辨會被別人同型號的機器帶偏——
    # 實測近 2 小時 stage / sit 各有 400 上下的 iPhone 15 流量，比大小等於擲骰子。
    cached_ad_id = None
    if args.udid and not args.ad_id:
        cached_ad_id = device_registry.fetch_ad_id(args.udid, args.platform or "ios")
        if cached_ad_id:
            args.ad_id = cached_ad_id
            print(f"(裝置註冊庫 udid → ad-id={cached_ad_id})")

    args.env = resolve_env(args)
    # prod 的門擋在建 client 之前：連線本身無害，但這道關卡要擋的是「順手就撈下去」。
    if is_prod(args.env):
        if not args.allow_prod:
            sys.exit(PROD_REFUSAL.format(url=resolve_kibana_url(args.env)))
        print(f"🚨 撈的是 prod（{resolve_kibana_url(args.env)}）—— 真實客戶資料，"
              f"時間窗壓小、輸出別外流。")
    client = KibanaClient.for_env(args.env)

    if args.email and not args.member_uuid:
        email = (default_email(args.platform or "ios", args.env)
                 if args.email == "auto" else args.email)
        if email:
            args.member_uuid = email_to_member_uuid(client, email, time_range(args))
            print(f"({email} → member_uuid={args.member_uuid!r})")
            if not args.member_uuid:
                print("  (這段時間窗內查不到該帳號的 member_uuid，把窗拉大再試)")

    if not args.list_devices:
        args.ad_id = resolve_identity(client, args, cached_ad_id)
        if args.ad_id and args.ad_id != cached_ad_id:
            print(f"(裝置識別 ad-id={args.ad_id})")

    must = base_filters(args)

    if args.list_devices:
        rng = time_range(args)
        print(f"=== env={args.env}  時間 {rng['gte']} → {rng['lte']}")
        list_devices(client, base_filters(argparse.Namespace(**{**vars(args), "device": None})))
        return

    if not args.detail:
        rows, n = route_counts(client, must, args)
        scope = f"member_uuid={args.member_uuid}" if args.member_uuid else "platform=APP"
        if args.platform:
            scope += f" source={'/'.join(SOURCE_VARIANTS[args.platform])}"
        if args.ad_id:
            scope += f" ad-id={args.ad_id}"
        elif args.device:
            scope += f" device={args.device!r}"
        rng = time_range(args)
        print(f"=== env={args.env} {scope}")
        print(f"    時間 {rng['gte']} → {rng['lte']}  ({n} docs, {len(rows)} routes)")
        for route, cnt in rows:
            print(f"  {cnt:>6}  {route}")
        if not rows:
            print("  (無資料：① --env 是否就是那台裝置實際打的環境（撈錯 env 回 0 筆，"
                  "跟『這段沒操作』長得一模一樣）② 核對上面那行時間範圍——@timestamp 是 UTC，"
                  "你給的時間已按本地時區換算 ③ 該帳號這段時間真的有操作)")
            list_devices(client, base_filters(
                argparse.Namespace(**{**vars(args), "device": None, "route": None})))
        return

    body_must = must + [{"exists": {"field": "request.body"}}]
    if args.device and not args.ad_id:
        # 只有 device-model 收斂時，ES 那層是會誤中同前綴型號的預篩——直接取樣會抓到別台的
        # contract（實測第一次就抓到 Simulator / locale=cn 那筆）。逐筆比完再取前三筆。
        samples = [src for src, _ in fetch_docs(client, body_must, "device-model", args.device)][:3]
    else:
        rv = client.search({"query": {"bool": {"must": body_must}},
                            "sort": [{"@timestamp": "desc"}], "size": 3})
        samples = [h.get("_source") or {} for h in (rv.get("hits") or {}).get("hits") or []]
    print(f"=== contract samples: {len(samples)}")
    for src in samples:
        req = src.get("request") or {}
        resp = src.get("response") or {}
        print(f"\n--- {req.get('method')} {req.get('url')}")
        print(f"    route      : {req.get('route')}")
        print(f"    request.uuid: {req.get('uuid')}   <- 用這個串 REQUEST/RESPONSE")
        hdrs = redact(parse_headers(req.get("headers")))
        print(f"    headers    : {json.dumps(hdrs, ensure_ascii=False)[:600]}")
        print(f"    body       : {str(req.get('body'))[:600]}")
        # 取樣取的是 REQUEST（要有 headers / body 才叫 contract），而 REQUEST 這筆身上沒有
        # response 欄位——回應是另一筆文件。所以這裡要自己去配對，不能等 src 裡有。
        if not resp:
            resp = fetch_response(client, args, req.get("uuid"), req.get("route"))
        if resp:
            status = f"HTTP {resp.get('http_status')}"
            meta = meta_status(resp)
            took = f" {resp.get('time')}ms" if resp.get("time") is not None else ""
            print(f"    response   : {status}{took}  {meta}")
            print(f"                 {str(redact(resp).get('body'))[:600]}")
        else:
            print("    response   : (配對不到——回應可能落在時間窗外，或這筆請求沒有回應)")


if __name__ == "__main__":
    main()
