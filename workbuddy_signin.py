#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy (CodeBuddy CN) 每日自动签到（零依赖，仅 Python 标准库）

机制（逆向自开源项目 Sliverkiss/workbuddy2api，实测值）：
  - 登录：设备流 POST /v2/plugin/auth/state 拿授权 URL → 浏览器登录 →
         轮询 GET /v2/plugin/auth/token?state= 自动拿到 token（无需手动粘贴）→
         GET /v2/plugin/login/account?state= 拿 uid/nickname → 落盘
  - 签到：POST www.codebuddy.cn/v2/billing/meter/daily-checkin（幂等，已签到返回 code=10001）
  - 刷新：token 将在 2h 内过期时 POST /v2/plugin/auth/token/refresh（X-Refresh-Token）换新
  - 鉴权：Authorization: Bearer <accessToken> + X-User-Id（+ X-Enterprise-Id/X-Tenant-Id/X-Domain）

用法：
  python3 workbuddy_signin.py login     # 首次/重新登录（浏览器登录，自动轮询，无需粘贴）
  python3 workbuddy_signin.py signin    # 刷新 token(按需) + 每日签到 + 查积分
  python3 workbuddy_signin.py            # 默认等于 signin

配合 launchd / run_all.py 每天定时跑 signin 即可（本文件丢进 scheduled-tasks/ 会被自动发现）。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta

# ───────────────────────── 常量（与 workbuddy2api 一致，实测值） ─────────────────────────
CHAT_BASE = "https://copilot.tencent.com"        # 鉴权 / 刷新 host
BILLING_BASE = "https://www.codebuddy.cn"         # 签到 / 积分 host
CLIENT_UA = "CLI/2.63.2 CodeBuddy/2.63.2"
ORIGIN = "https://www.codebuddy.cn"
AUTH_FILE = os.path.expanduser("~/.workbuddy_auth.json")

EP_AUTH_STATE = CHAT_BASE + "/v2/plugin/auth/state?platform=CLI"
EP_AUTH_TOKEN = CHAT_BASE + "/v2/plugin/auth/token?state="
EP_AUTH_ACCOUNT = CHAT_BASE + "/v2/plugin/login/account?state="
EP_REFRESH = CHAT_BASE + "/v2/plugin/auth/token/refresh"
EP_CHECKIN = BILLING_BASE + "/v2/billing/meter/daily-checkin"
EP_RESOURCE = BILLING_BASE + "/v2/billing/meter/get-user-resource"


class ApiError(Exception):
    def __init__(self, code, msg, status, raw=""):
        self.code = code
        self.msg = msg
        self.status = status
        self.raw = raw
        super().__init__(f"code={code} msg={msg} (http {status})")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────── HTTP ─────────────────────────
def common_headers():
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "User-Agent": CLIENT_UA,
    }


def billing_headers(auth):
    h = {
        "Authorization": "Bearer " + auth["auth"].get("accessToken", ""),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if auth["account"].get("uid"):
        h["X-User-Id"] = auth["account"]["uid"]
    if auth["account"].get("enterpriseId"):
        h["X-Enterprise-Id"] = auth["account"]["enterpriseId"]
        h["X-Tenant-Id"] = auth["account"]["enterpriseId"]
    if auth["auth"].get("domain"):
        h["X-Domain"] = auth["auth"]["domain"]
    return h


def refresh_headers(auth):
    h = common_headers()
    h["X-Refresh-Token"] = auth["auth"].get("refreshToken", "")
    if auth["account"].get("enterpriseId"):
        h["X-Enterprise-Id"] = auth["account"]["enterpriseId"]
    h["X-Auth-Refresh-Source"] = "workbuddy"
    return h


def api_req(method, url, headers, body=None, retries=2):
    """发请求并解 {code,msg,data} 信封；HTTP>=400 或 code!=0 → 抛 ApiError；成功返回 data 字段。"""
    last_exc = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace") or "{}"
                obj = json.loads(raw)
                code = obj.get("code", 0)
                if code != 0:
                    raise ApiError(code, obj.get("msg", ""), resp.status, raw)
                return obj.get("data", obj)
        except ApiError:
            raise
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                obj = json.loads(raw or "{}")
            except Exception:
                obj = {}
            raise ApiError(obj.get("code", e.code), obj.get("msg", raw[:200]), e.code, raw)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(1)
                continue
            raise
    if last_exc:
        raise last_exc
    return {}


# ───────────────────────── 登录（设备流，自动轮询） ─────────────────────────
def poll_token(state, timeout=150):
    """轮询 /v2/plugin/auth/token?state= 直到拿到 accessToken（登录未完成时 code!=0，继续等）。"""
    url = EP_AUTH_TOKEN + urllib.parse.quote(state, safe="")
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            data = api_req("GET", url, common_headers())
        except ApiError as e:
            print(f"  [轮询 #{attempt}] 登录尚未完成（{e.msg or 'pending'}），3s 后重试…", flush=True)
            time.sleep(3)
            continue
        except Exception as e:
            print(f"  [轮询 #{attempt}] 请求异常: {e}，3s 后重试…", flush=True)
            time.sleep(3)
            continue
        if data.get("accessToken"):
            print(f"  [轮询 #{attempt}] 已获取 token ✓", flush=True)
            return data
        time.sleep(3)
    print("登录超时：请确认已在浏览器完成登录后重新运行 login", flush=True)
    sys.exit(1)


def cmd_login():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    print("=" * 60, flush=True)
    print("  WorkBuddy 登录（设备流，自动轮询，无需手动粘贴）", flush=True)
    print("=" * 60, flush=True)

    # 1) 取授权 URL
    st = api_req("POST", EP_AUTH_STATE, common_headers(), b"{}")
    state = st.get("state", "")
    auth_url = st.get("authUrl", "")
    if not state or not auth_url:
        print("auth/state 未返回 state/authUrl，无法继续")
        sys.exit(1)

    print("\n请在浏览器打开以下链接完成登录：\n")
    print("  " + auth_url + "\n")
    try:
        webbrowser.open(auth_url)
        print("（已尝试自动打开浏览器）", flush=True)
    except Exception:
        pass
    print("登录完成后本脚本会自动轮询获取 token，请稍候…\n", flush=True)

    # 2) 轮询拿 token
    tok = poll_token(state)

    # 3) 取账号信息
    acct = {}
    try:
        acct = api_req(
            "GET", EP_AUTH_ACCOUNT + urllib.parse.quote(state, safe=""),
            {**common_headers(), "Authorization": "Bearer " + tok.get("accessToken", "")},
        )
    except Exception as e:
        print(f"[*] 获取账号信息失败（用 token 继续）: {e}", flush=True)

    uid = acct.get("uid", "") or tok.get("uid", "")
    enterprise_id = acct.get("enterpriseId", "")
    nickname = acct.get("nickname", "")
    access = tok.get("accessToken", "")
    refresh = tok.get("refreshToken", "")
    domain = tok.get("domain", "")
    expires_in = int(tok.get("expiresIn", 0) or 0)
    expires_at = int(time.time()) + expires_in if expires_in > 0 else int(time.time()) + 1209600

    if not uid or not access:
        print("未能获取 uid / accessToken，请检查登录是否完成")
        sys.exit(1)

    save = {
        "account": {"uid": uid, "enterpriseId": enterprise_id, "nickname": nickname},
        "auth": {
            "accessToken": access,
            "refreshToken": refresh,
            "expiresAt": expires_at,
            "domain": domain,
        },
    }
    with open(AUTH_FILE, "w") as f:
        json.dump(save, f, indent=2, ensure_ascii=False)
    os.chmod(AUTH_FILE, 0o600)
    print(f"\n登录成功！uid={uid} nickname={nickname}")
    print(f"凭证已保存（0600）：{AUTH_FILE}")
    print(f"有效期至：{datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')}")

    # 顺手签到一次
    cmd_signin()


# ───────────────────────── 签到（每日） ─────────────────────────
def load_auth():
    if not os.path.exists(AUTH_FILE):
        print(f"未登录，请先运行：python3 {os.path.abspath(__file__)} login")
        sys.exit(1)
    with open(AUTH_FILE, "r") as f:
        return json.load(f)


def user_resource(auth):
    """聚合剩余可花费积分（CycleCapacityRemain）。"""
    now = datetime.now()
    body = {
        "PageNumber": 1,
        "PageSize": 100,
        "ProductCode": "p_tcaca",
        "Status": [0, 3],
        "PackageEndTimeRangeBegin": now.strftime("%Y-%m-%d %H:%M:%S"),
        "PackageEndTimeRangeEnd": (now + timedelta(days=365 * 101)).strftime("%Y-%m-%d %H:%M:%S"),
    }
    data = api_req("POST", EP_RESOURCE, billing_headers(auth), json.dumps(body).encode())
    resp = data.get("Response", data) if isinstance(data, dict) else {}
    accounts = (resp.get("Data") or {}).get("Accounts", []) if isinstance(resp, dict) else []
    remain = 0
    for acct in accounts:
        r = int(acct.get("CycleCapacityRemain", 0) or 0)
        remain += r if r > 0 else 0
    return remain


def refresh_if_needed(auth):
    """token 将在 2h 内过期（或已过期/无 expiry）时刷新；成功写回 AUTH_FILE，返回是否刷新。"""
    exp = auth["auth"].get("expiresAt", 0)
    if exp and time.time() + 7200 < exp:
        return False
    rt = auth["auth"].get("refreshToken", "")
    if not rt:
        return False
    try:
        data = api_req("POST", EP_REFRESH, refresh_headers(auth), b"")
    except Exception as e:
        print(f"[{now_str()}] refresh 失败（需重新 login）: {e}", flush=True)
        return False
    if not data.get("accessToken"):
        print(f"[{now_str()}] refresh 返回无 accessToken，需重新 login", flush=True)
        return False
    auth["auth"]["accessToken"] = data["accessToken"]
    if data.get("refreshToken"):
        auth["auth"]["refreshToken"] = data["refreshToken"]
    if data.get("domain"):
        auth["auth"]["domain"] = data["domain"]
    ei = int(data.get("expiresIn", 0) or 0)
    if ei > 0:
        auth["auth"]["expiresAt"] = int(time.time()) + ei
    with open(AUTH_FILE, "w") as f:
        json.dump(auth, f, indent=2, ensure_ascii=False)
    os.chmod(AUTH_FILE, 0o600)
    return True


def cmd_signin():
    auth = load_auth()
    if refresh_if_needed(auth):
        print(f"[{now_str()}] token 已刷新", flush=True)

    # 1) 签到
    try:
        api_req("POST", EP_CHECKIN, billing_headers(auth), b"{}")
        print(f"[{now_str()}] 签到成功 ✓", flush=True)
    except ApiError as e:
        msg = e.msg or ""
        if "已签到" in msg or "already" in msg.lower() or "checkin" in msg.lower() or "10001" in msg:
            print(f"[{now_str()}] 今日已签到，跳过", flush=True)
        else:
            print(f"[{now_str()}] 签到失败: code={e.code} msg={msg}", flush=True)
    except Exception as e:
        print(f"[{now_str()}] 签到异常: {e}", flush=True)

    # 2) 查积分
    try:
        remain = user_resource(auth)
        print(f"[{now_str()}] 当前剩余积分: {remain}", flush=True)
    except Exception as e:
        print(f"[{now_str()}] 查询积分失败: {e}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="WorkBuddy 每日自动签到")
    parser.add_argument("cmd", nargs="?", default="signin", choices=["login", "signin"],
                        help="login=首次登录; signin=每日签到(默认)")
    args = parser.parse_args()
    if args.cmd == "login":
        cmd_login()
    else:
        cmd_signin()


if __name__ == "__main__":
    main()
