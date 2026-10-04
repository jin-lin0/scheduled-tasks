#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TRAE Work 每日自动签到（零依赖，仅 Python 标准库）

机制（来自开源项目 traework2api 实测）：
  - 登录：浏览器手机号登录 → 回调取 refreshToken/userInfo → ExchangeToken 换 accessToken → 落盘
  - 签到：POST /trae/api/v2/ug/checkin_credits/status 查状态 → 未签到则 claim 领取 200 积分
  - 鉴权：Authorization: Cloud-IDE-JWT <accessToken>  +  X-User-Region: CN  +  X-Device-Id

用法：
  python3 trae_work_signin.py login     # 首次/重新登录（交互，需浏览器+手机验证码）
  python3 trae_work_signin.py signin    # 刷新 token(按需) + 查询/执行签到 + 查积分
  python3 trae_work_signin.py            # 默认等于 signin

建议配合 launchd 每天定时跑 signin（见同目录 com.hejinlin.traework.signin.plist）。
"""

import argparse
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime

# ───────────────────────── 常量（与 traework2api 一致，实测值） ─────────────────────────
CLIENT_ID = "en1oxy7wnw8j9n"          # SOLO stable client_id
APP_VERSION = "0.1.43"
API_HOST = "https://api.trae.com.cn"  # ExchangeToken / GetUserInfo host
UG_HOST = "https://api.trae.cn"       # 签到 / 积分 host
AUTH_FILE = os.path.expanduser("~/.trae_work_auth.json")
EP_EXCHANGE = "/cloudide/api/v3/trae/oauth/ExchangeToken"
EP_USERINFO = "/cloudide/api/v3/trae/GetUserInfo"
EP_CHECKIN_STATUS = "/trae/api/v2/ug/checkin_credits/status"
EP_CHECKIN_CLAIM = "/trae/api/v2/ug/checkin_credits/claim"
EP_ENT_USAGE = "/trae/api/v2/pay/ide_user_ent_usage"


# ───────────────────────── 基础 HTTP ─────────────────────────
def http_post(url, body, headers, timeout=30, retries=2):
    """POST JSON，返回 (obj, status)。非 2xx 也返回解析后的 body 与状态码，由调用方判错。"""
    data = json.dumps(body).encode()
    last_exc = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}"), resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                obj = json.loads(raw or "{}")
            except Exception:
                obj = {"_raw": raw}
            if e.code >= 500 and attempt < retries:
                time.sleep(1)
                continue
            return obj, e.code
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(1)
                continue
            raise
    if last_exc:
        raise last_exc
    return {}, 0


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────── 登录（一次性） ─────────────────────────
def _capture_callback(timeout=150):
    """本地起 127.0.0.1:18080 的 HTTP 服务，自动接收 TRAE 登录回调。
    返回完整回调 path（含 query，如 /authorize?refreshToken=...）；超时/端口占用返回 None。"""
    state = {"path": None, "event": threading.Event()}

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state["path"] = self.path
            state["event"].set()
            html = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>TRAE 登录成功</title></head><body style='font-family:"
                "-apple-system,sans-serif;display:flex;height:100vh;align-items:"
                "center;justify-content:center'><div style='text-align:center'>"
                "<h2>✓ TRAE 登录成功</h2><p>可以关闭此页面，回到终端继续。</p>"
                "</div></body></html>"
            )
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            except Exception:
                pass

        def log_message(self, *a):
            pass

    try:
        srv = http.server.HTTPServer(("127.0.0.1", 18080), _H)
    except OSError as e:
        print("[*] 无法在 127.0.0.1:18080 监听（" + str(e) + "），将改用手动粘贴。", flush=True)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ok = state["event"].wait(timeout)
    srv.shutdown()
    srv.server_close()
    return state["path"] if ok else None


def cmd_login():
    # 强制行缓冲，避免某些终端/管道下 print 被块缓冲导致"回车后没反应"
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    machine_id = secrets.token_hex(16)
    device_id = secrets.token_hex(16)
    params = {
        "login_version": "1",
        "auth_from": "solo",
        "login_channel": "native_ide",
        "plugin_version": "2.3.62834",
        "auth_type": "local",
        "client_id": CLIENT_ID,
        "redirect": "0",
        "login_trace_id": secrets.token_hex(8),
        "auth_callback_url": "http://127.0.0.1:18080/authorize",
        "machine_id": machine_id,
        "device_id": device_id,
        "x_device_id": device_id,
        "x_machine_id": machine_id,
        "x_device_brand": "PC",
        "x_device_type": "PC",
        "x_os_version": "1.0",
        "x_app_version": APP_VERSION,
        "x_app_type": "stable",
    }
    url = "https://www.trae.cn/authorization?" + urllib.parse.urlencode(params)
    print("=" * 60)
    print("  TRAE Work 登录")
    print("=" * 60)
    print("\n步骤：")
    print("  1. 在浏览器打开下面的链接，用手机号/验证码登录")
    print("  2. 登录成功后浏览器会跳到打不开的 127.0.0.1 地址（正常）")
    print("  3. 复制浏览器地址栏的完整链接，粘贴到下面")
    print("\n请打开登录链接（脚本已尝试自动打开浏览器）：\n")
    print("  " + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("浏览器登录后，TRAE 会自动跳转回 http://127.0.0.1:18080 ，本脚本将自动接收回调（无需手动粘贴）。", flush=True)
    cb = _capture_callback(timeout=150)
    if not cb:
        print("[*] 150 秒内未自动收到回调，改为手动粘贴。", flush=True)
        cb = input("请粘贴浏览器地址栏的完整回调链接，然后按回车：").strip()
        sys.stdout.write("✓ 已读到 " + str(len(cb)) + " 个字符的回调链接\n")
        sys.stdout.flush()
    if not cb:
        print("未输入回调链接，已取消")
        sys.exit(1)
    print("正在用 refreshToken 换取 accessToken，请稍候…", flush=True)

    qs = urllib.parse.parse_qs(urllib.parse.urlparse(cb).query)
    refresh_token = (qs.get("refreshToken") or [""])[0]

    def parse_json_param(raw):
        if not raw:
            return {}
        for v in (raw, urllib.parse.unquote(raw)):
            try:
                o = json.loads(v)
                if isinstance(o, dict):
                    return o
            except Exception:
                pass
        return {}

    user_info = parse_json_param((qs.get("userInfo") or [""])[0])
    user_jwt = parse_json_param((qs.get("userJwt") or [""])[0])
    uid = str(user_info.get("UserID") or "")
    nickname = str(user_info.get("ScreenName") or "")
    if not refresh_token:
        refresh_token = str(user_jwt.get("RefreshToken") or "")

    token, new_refresh, expires_at = "", refresh_token, 0
    if refresh_token:
        body = {"ClientID": CLIENT_ID, "RefreshToken": refresh_token, "ClientSecret": "-", "UserID": ""}
        resp, _ = http_post(
            API_HOST + EP_EXCHANGE, body,
            {"Content-Type": "application/json", "User-Agent": f"Trae/{APP_VERSION}"},
        )
        result = resp.get("Result") or {}
        token = result.get("Token") or ""
        print("[*] accessToken 已换取成功（长度 " + str(len(token)) + "）", flush=True)
        if not token:
            print("ExchangeToken 失败: " + json.dumps(resp, ensure_ascii=False)[:300])
            sys.exit(1)
        new_refresh = result.get("RefreshToken") or refresh_token
        expires_at = int(result.get("TokenExpireAt") or 0)
        if expires_at > 10 ** 12:  # 毫秒 → 秒
            expires_at //= 1000
        if expires_at <= time.time():
            expires_at = int(time.time()) + int(result.get("TokenExpireDuration") or 1209600)
    else:
        token = str(user_jwt.get("Token") or "")
        expires_at = int(user_jwt.get("TokenExpireAt") or 0)
        if expires_at > 10 ** 12:
            expires_at //= 1000
        if not token:
            print("回调缺少 refreshToken，且 userJwt 也没有 Token")
            sys.exit(1)

    # GetUserInfo 补 EnterpriseID（失败不阻塞）
    try:
        ui, _ = http_post(
            API_HOST + EP_USERINFO, {"ReqSource": "IDE", "IDEVersion": APP_VERSION},
            {"Content-Type": "application/json", "X-Cloudide-Token": token,
             "User-Agent": f"Trae/{APP_VERSION}"},
        )
        u = ui.get("Result") or ui
        if u.get("UserID"):
            uid = str(u.get("UserID") or uid)
            nickname = str(u.get("ScreenName") or nickname)
    except Exception as e:
        print(f"[*] GetUserInfo 失败（使用回调信息）: {e}")

    if not uid:
        print("未能获取 uid，请检查回调链接是否完整")
        sys.exit(1)

    save = {
        "account": {"uid": uid, "enterpriseId": "", "nickname": nickname},
        "auth": {
            "accessToken": token,
            "refreshToken": new_refresh,
            "expiresAt": expires_at,
            "domain": "trae.cn",
            "apiHost": API_HOST,
            "machineId": machine_id,
            "deviceId": device_id,
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


def ug_headers(token, device_id):
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"Trae/{APP_VERSION}",
        "Authorization": f"Cloud-IDE-JWT {token}",
        "X-User-Region": "CN",
        "X-Device-Id": device_id or "",
    }


def refresh_if_needed(auth):
    """token 将在 2h 内过期（或已过期/无 expiry）时刷新；成功写回 AUTH_FILE，返回是否刷新。"""
    exp = auth["auth"].get("expiresAt", 0)
    if exp and time.time() + 7200 < exp:
        return False
    rt = auth["auth"].get("refreshToken", "")
    if not rt:
        return False
    body = {"ClientID": CLIENT_ID, "RefreshToken": rt, "ClientSecret": "-", "UserID": ""}
    try:
        resp, _ = http_post(
            API_HOST + EP_EXCHANGE, body,
            {"Content-Type": "application/json", "User-Agent": f"Trae/{APP_VERSION}"},
        )
    except Exception as e:
        print(f"[{now_str()}] refresh 请求异常: {e}")
        return False
    result = resp.get("Result") or {}
    token = result.get("Token") or ""
    if not token:
        print(f"[{now_str()}] refresh 失败（需重新 login）: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return False
    auth["auth"]["accessToken"] = token
    if result.get("RefreshToken"):
        auth["auth"]["refreshToken"] = result["RefreshToken"]
    ea = int(result.get("TokenExpireAt") or 0)
    if ea > 10 ** 12:
        ea //= 1000
    if ea > 0:
        auth["auth"]["expiresAt"] = ea
    with open(AUTH_FILE, "w") as f:
        json.dump(auth, f, indent=2, ensure_ascii=False)
    os.chmod(AUTH_FILE, 0o600)
    return True


def cmd_signin():
    auth = load_auth()
    device_id = auth["auth"].get("deviceId", "")
    token = auth["auth"].get("accessToken", "")

    if refresh_if_needed(auth):
        token = auth["auth"]["accessToken"]
        print(f"[{now_str()}] token 已刷新")

    h = ug_headers(token, device_id)

    # 1) 查状态
    st, code = http_post(UG_HOST + EP_CHECKIN_STATUS, {}, h)
    if code == 401:
        print(f"[{now_str()}] token 失效（http 401），请重新运行 login")
        sys.exit(1)
    checked_in = st.get("checked_in")
    credits = st.get("credits")
    enable = st.get("enable")
    print(f"[{now_str()}] 状态: checked_in={checked_in} 今日积分={credits} enable={enable} (http {code})")

    # 2) 领取（服务端限流 9074/频繁/太多 → 等待重试，最多 3 轮）
    if checked_in:
        print(f"[{now_str()}] 今日已签到，跳过")
    elif enable is False:
        print(f"[{now_str()}] 签到未开启(enable=false)，跳过")
    else:
        claim_ok = False
        for attempt in range(1, 4):
            r, cc = http_post(UG_HOST + EP_CHECKIN_CLAIM, {}, h)
            msg = r.get("message") or r.get("msg") or json.dumps(r, ensure_ascii=False)
            code = r.get("code")
            print(f"[{now_str()}] 签到结果(第{attempt}次): {msg} (http {cc}, code={code})")
            # 成功：code=0 或返回的 message 为 success / 已签到
            if code == 0 or msg == "success" or "已签到" in str(msg):
                claim_ok = True
                break
            # 限流类：9074 参与用户太多 / 操作太过频繁 / 稍后再试 → 等待后重试
            is_rate = (
                code == 9074
                or any(k in str(msg) for k in ("频繁", "太多", "稍后再试", "高峰期", "重试"))
            )
            if is_rate and attempt < 3:
                wait = 60 * attempt  # 60s / 120s
                print(f"[{now_str()}] 命中服务端限流，{wait}s 后重试…", flush=True)
                time.sleep(wait)
                continue
            break
        if not claim_ok:
            print(f"[{now_str()}] 今日 TRAE 签到未成功（服务端限流），将由下次定时触发自动补签", flush=True)

    # 3) 查总积分
    try:
        ent, _ = http_post(UG_HOST + EP_ENT_USAGE, {}, h)
        packs = ent.get("user_entitlement_pack_list") or []
        total = sum(
            p.get("entitlement_base_info", {}).get("quota", {}).get("credits_limit", 0)
            for p in packs
        )
        print(f"[{now_str()}] 当前积分总额: {total}")
    except Exception as e:
        print(f"[{now_str()}] 查积分失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="TRAE Work 每日自动签到")
    parser.add_argument("cmd", nargs="?", default="signin", choices=["login", "signin"],
                        help="login=首次登录; signin=每日签到(默认)")
    args = parser.parse_args()
    if args.cmd == "login":
        cmd_login()
    else:
        cmd_signin()


if __name__ == "__main__":
    main()
