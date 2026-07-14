#!/usr/bin/env python3
"""check_alive.py — grok2api SSO token 测活 + 存活账号上传 CPA。

完整流程：
  注册机产出 accounts.txt (email----password----sso)
    → 逐个 SSO → device flow 换 token → 调 grok-4.5 build API 测活
    → 存活的（200 模型回答）→ 组装 CPA 记录 → 上传远程 CPA

判定：
  200 -> 活（模型回答）-> 测活通过 -> 上传 CPA
  429 / 403+CF / 401 / 其他 -> 死 -> 不上传

输出：
  grok2api_tokens.txt      存活 SSO token (每行一个)
  verify_report.json       完整验证明细

用法:
  python check_alive.py --input accounts.txt
  python check_alive.py --workers 3 --proxy http://127.0.0.1:7897
  python check_alive.py --input all_accounts.txt --cpa-url http://x:8317 --cpa-key xxx
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# ====================== 配置 ======================
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = ("openid profile email offline_access "
         "grok-cli:access api:access conversations:read conversations:write")
DEFAULT_PROXY = ""
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BUILD_UA = "grok-shell/0.2.99 (linux; x86_64)"

# CPA 记录常量（与 sso_to_auth_json.py 对齐）
CPA_GROK_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CPA_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:56121/callback"
GROK_VERSION = "0.2.99"
GROK_TOKEN_UA = "grok-shell/0.2.99 (linux; x86_64)"


def parse_accounts(path: Path):
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) < 3:
            continue
        out.append({"email": parts[0].strip(),
                    "password": parts[1].strip(),
                    "sso": "----".join(parts[2:]).strip()})
    return out


def sso_to_oauth(sso: str, proxies, timeout: float):
    """SSO cookie -> OAuth access_token + refresh_token via device flow."""
    sess = requests.Session()
    sess.proxies = proxies
    sess.headers["User-Agent"] = UA
    sess.cookies.set("sso", sso, domain=".x.ai")
    sess.cookies.set("sso-rw", sso, domain=".x.ai")

    # 1. validate session
    r = sess.get("https://accounts.x.ai/", timeout=timeout, allow_redirects=True)
    if "sign-in" in r.url.lower() or r.status_code == 401:
        return None, "sso-invalid"

    # 2. device code
    r = sess.post("https://auth.x.ai/oauth2/device/code",
                  data={"client_id": CLIENT_ID, "scope": SCOPE},
                  headers={"Accept": "application/json"}, timeout=timeout)
    try:
        dc = r.json()
    except Exception:
        return None, f"device-code-parse-fail:{r.status_code}"
    if "device_code" not in dc:
        return None, f"device-code-error:{dc.get('error', r.status_code)}"
    device_code = dc["device_code"]
    user_code = dc["user_code"]
    verify_url = dc["verification_uri_complete"]

    # 3-4. verify
    sess.get(verify_url, timeout=timeout, allow_redirects=True)
    sess.post("https://auth.x.ai/oauth2/device/verify",
              data={"user_code": user_code}, timeout=timeout, allow_redirects=True)

    # 5. approve
    sess.post("https://auth.x.ai/oauth2/device/approve",
              data={"user_code": user_code, "action": "allow",
                    "principal_type": "User", "principal_id": ""},
              timeout=timeout, allow_redirects=True)

    # 6. poll token
    for _ in range(15):
        r = sess.post("https://auth.x.ai/oauth2/token",
                      data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            "client_id": CLIENT_ID, "device_code": device_code},
                      headers={"Accept": "application/json"}, timeout=timeout)
        td = r.json()
        if "access_token" in td:
            if "token_type" not in td:
                td["token_type"] = "Bearer"
            if "expires_in" not in td:
                td["expires_in"] = 21600
            return td, "ok"
        err = td.get("error", "")
        if err == "expired_token":
            return None, "device-expired"
        time.sleep(td.get("interval", 5))
    return None, "poll-timeout"


def test_build_api(access_token: str, proxies, timeout: float, model="grok-4.5"):
    """用 access_token 调 grok build API，返回 (status_code, body_snippet)。"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "Content-Type": "application/json",
        "x-grok-client-version": "0.2.99",
        "x-grok-client-identifier": "grok-shell",
        "x-grok-client-surface": "tui",
        "x-grok-client-name": "grok-shell",
        "User-Agent": BUILD_UA,
    }
    r = requests.post("https://cli-chat-proxy.grok.com/v1/chat/completions",
                      headers=headers,
                      json={"model": model,
                            "messages": [{"role": "user", "content": "1+1=?"}],
                            "max_tokens": 10, "stream": False},
                      proxies=proxies, timeout=timeout)
    return r.status_code, r.text[:300]


def decode_jwt_payload(token: str) -> dict:
    """解码 JWT payload（不验签）。"""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        seg = parts[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def _iso_utc_from_unix(ts) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def build_cpa_record(token: dict, email: str, sso: str) -> dict:
    """device flow token → CPA 扁平 xai auth 记录。"""
    from datetime import datetime, timezone
    access = token.get("access_token", "")
    refresh = token.get("refresh_token", "")
    payload = decode_jwt_payload(access)
    sub = payload.get("sub", "")
    expired = ""
    if "exp" in payload:
        expired = _iso_utc_from_unix(payload["exp"])
    elif token.get("expires_in"):
        expired = _iso_utc_from_unix(int(time.time()) + int(token["expires_in"]))
    record = {
        "type": "xai",
        "auth_kind": "oauth",
        "email": email or "",
        "sub": sub,
        "access_token": access,
        "refresh_token": refresh,
        "id_token": token.get("id_token", ""),
        "token_type": token.get("token_type", "Bearer"),
        "expires_in": token.get("expires_in"),
        "expired": expired,
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "redirect_uri": REDIRECT_URI,
        "token_endpoint": CPA_TOKEN_ENDPOINT,
        "base_url": CPA_GROK_BASE_URL,
        "disabled": False,
        "headers": {
            "User-Agent": GROK_TOKEN_UA,
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-authenticateresponse": "authenticate-response",
            "x-grok-client-identifier": "grok-pager",
            "x-grok-client-version": GROK_VERSION,
        },
    }
    if sso:
        record["sso"] = sso
    return record


def upload_to_cpa(cpa_url: str, cpa_key: str, record: dict, timeout: int = 30) -> str:
    """上传账号到 grok2api admin 接口（multipart/form-data）。

    POST {cpa_url}/api/admin/v1/accounts/import
    Header: authorization: Bearer {cpa_key}
    Body: multipart form, files = xai-<email>.json
    """
    base = cpa_url.strip().rstrip("/")
    email = str(record.get("email") or record.get("sub") or "unknown")
    safe = "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in email)
    fname = safe if safe.lower().startswith("xai") else f"xai-{safe}"
    fname = f"{fname}.json"
    url = f"{base}/api/admin/v1/accounts/import"
    file_content = json.dumps(record, ensure_ascii=False).encode("utf-8")
    resp = requests.post(
        url,
        headers={"authorization": f"Bearer {cpa_key}"},
        files={"files": (fname, file_content, "application/json")},
        timeout=timeout,
        verify=False,
    )
    if resp.status_code >= 400:
        body = (resp.text or "").strip()[:200]
        raise RuntimeError(f"CPA HTTP {resp.status_code}: {body}")
    return fname


def check_one(acc: dict, proxies, timeout: float,
              cpa_url: str = "", cpa_key: str = "") -> dict:
    r = {**acc, "ok": False, "detail": "", "build_code": None,
         "access_token": "", "refresh_token": "", "cpa_uploaded": False}
    # SSO -> OAuth
    td, msg = sso_to_oauth(acc["sso"], proxies, timeout)
    if td is None:
        r["detail"] = f"oauth-fail:{msg}"
        return r
    r["access_token"] = td["access_token"]
    r["refresh_token"] = td.get("refresh_token", "")
    # build API test
    code, body = test_build_api(td["access_token"], proxies, timeout)
    r["build_code"] = code
    if code == 200:
        r["ok"] = True
        r["detail"] = "answered"
    elif code == 429:
        r["ok"] = False
        r["detail"] = "quota-exhausted"
    elif code == 403:
        if "cloudflare" in body.lower() or "just a moment" in body.lower():
            r["ok"] = False
            r["detail"] = "cf-blocked"
        else:
            r["detail"] = f"forbidden:{body[:80]}"
    elif code in (400, 401):
        r["detail"] = f"auth-fail:{code}"
    else:
        r["detail"] = f"http-{code}"

    # 存活才上传 CPA
    if r["ok"] and cpa_url and cpa_key:
        try:
            record = build_cpa_record(td, acc["email"], acc["sso"])
            fname = upload_to_cpa(cpa_url, cpa_key, record)
            r["cpa_uploaded"] = True
            r["detail"] += f" | CPA uploaded: {fname}"
        except Exception as exc:
            r["detail"] += f" | CPA upload FAIL: {exc}"
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="grok2api SSO 测活 + CPA 上传 (grok build + grok-4.5)")
    ap.add_argument("--input", "-i", default="accounts.txt")
    ap.add_argument("--tokens-out", default="grok2api_tokens.txt")
    ap.add_argument("--report", default="verify_report.json")
    ap.add_argument("--proxy", "-p", default=DEFAULT_PROXY)
    ap.add_argument("--workers", "-w", type=int, default=3)
    ap.add_argument("--timeout", "-t", type=float, default=25.0)
    ap.add_argument("--cpa-url", default=os.environ.get("CPA_URL", ""))
    ap.add_argument("--cpa-key", default=os.environ.get("CPA_KEY", ""))
    args = ap.parse_args()

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    src = Path(args.input)
    if not src.is_file():
        raise SystemExit(f"找不到输入文件: {src}")
    accounts = parse_accounts(src)
    total = len(accounts)
    print(f"输入: {args.input} ({total} 个账号)")
    print(f"流程: SSO -> device flow OAuth -> grok-4.5 build API")
    print(f"代理: {args.proxy or '(直连)'}  并发: {args.workers}")
    if args.cpa_url:
        print(f"CPA: 存活账号自动上传到 {args.cpa_url}")
    print()

    results, done = [], 0
    t0 = time.time()
    # 串行测活：不并发，每个账号测完等 5 秒，避免触发 Cloudflare 429
    for acc in accounts:
        r = check_one(acc, proxies, args.timeout, args.cpa_url, args.cpa_key)
        results.append(r)
        done += 1
        tag = "OK " if r["ok"] else "BAD"
        print(f"[{done:>3}/{total}] {tag} {r['email']} -> {r['detail']}", flush=True)
        if done < total:
            time.sleep(5)

    elapsed = time.time() - t0
    order = {a["email"]: i for i, a in enumerate(accounts)}
    results.sort(key=lambda r: order.get(r["email"], 1e9))

    valid = [r for r in results if r["ok"]]
    invalid = [r for r in results if not r["ok"]]

    Path(args.tokens_out).write_text(
        "\n".join(r["sso"] for r in valid) + "\n", encoding="utf-8")
    Path(args.report).write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    uploaded = sum(1 for r in valid if r.get("cpa_uploaded"))
    print("\n" + "=" * 56)
    print(f"总计={total}  存活={len(valid)}  失效={len(invalid)}  耗时={elapsed:.1f}s")
    print(f"  CPA 上传成功: {uploaded}")
    print(f"-> 存活 SSO token -> {args.tokens_out}")
    print(f"-> 明细 -> {args.report}")
    if invalid:
        print(f"\n失效 ({len(invalid)}):")
        for r in invalid:
            print(f"  {r['email']:30s} {r['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
