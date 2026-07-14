#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时邮箱调试工具：创建邮箱、轮询收件箱、提取验证码。

对应新临时邮箱接口（无鉴权）：
  POST /api/v1/addresses   创建邮箱
  GET  /api/v1/{token}/emails  读取收件箱
"""

import argparse
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_API_URL = "https://mail.minecraft-cn.net"
DEFAULT_DOMAIN = "olsbvgq.shop"


def extract_code(text: str, subject: str = "") -> Optional[str]:
    """提取 Grok 验证码：优先 XXX-XXX 格式，兜底 4-8 位数字。"""
    for src in (subject, text):
        if not src:
            continue
        m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", src, re.IGNORECASE)
        if m:
            return m.group(1)
    for p in [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def json_or_text(resp: requests.Response) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data, ""
        return {"raw": data}, ""
    except Exception:
        return None, (resp.text or "")[:400]


def create_address(
    api_base: str,
    domain: str = DEFAULT_DOMAIN,
    username: str = "",
) -> Tuple[str, str]:
    """创建临时邮箱：POST /api/v1/addresses → 返回 (email, token)。"""
    name = (username or "").strip() or ("u_" + secrets.token_hex(6))
    resp = requests.post(
        f"{api_base.rstrip('/')}/api/v1/addresses",
        json={"username": name, "domain": domain},
        timeout=20,
        verify=False,
    )
    resp.raise_for_status()
    data, raw = json_or_text(resp)
    if not data:
        raise RuntimeError(f"/api/v1/addresses 非JSON: {raw}")
    email = str(data.get("email", "")).strip()
    token = str(data.get("token", "")).strip()
    if not email or not token:
        raise RuntimeError(f"/api/v1/addresses 缺少 email/token: {data}")
    return email, token


def fetch_inbox(api_base: str, token: str) -> List[Dict[str, Any]]:
    """读取收件箱：GET /api/v1/{token}/emails。"""
    resp = requests.get(
        f"{api_base.rstrip('/')}/api/v1/{token}/emails",
        timeout=20,
        verify=False,
    )
    if resp.status_code >= 400:
        return []
    data, _ = json_or_text(resp)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("emails"), list):
        return data["emails"]
    return []


def flatten_mail_text(item: Dict[str, Any]) -> Tuple[str, str]:
    subject = str(item.get("subject") or "")
    body = str(item.get("body") or "")
    text = "\n".join([subject, re.sub(r"<[^>]+>", " ", body)])
    return subject, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default=DEFAULT_API_URL)
    ap.add_argument("--domain", default=DEFAULT_DOMAIN)
    ap.add_argument("--username", default="")
    ap.add_argument("--token", default="", help="已有 token 则跳过创建直接轮询")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--interval", type=int, default=3)
    args = ap.parse_args()

    api_base = args.api_base.strip() or DEFAULT_API_URL
    domain = args.domain.strip() or DEFAULT_DOMAIN
    token = args.token.strip()

    if token:
        print(f"[USE] token={token}")
    else:
        email, token = create_address(api_base, domain=domain, username=args.username)
        print(f"[NEW] email={email}")
        print(f"[NEW] token={token}")

    deadline = time.time() + max(args.timeout, 1)
    seen_ids = set()
    while time.time() < deadline:
        mails = fetch_inbox(api_base, token)
        if mails:
            print(f"[INBOX] {len(mails)} mail(s)")
        for m in mails:
            mail_id = m.get("id") or m.get("_id")
            if not mail_id or mail_id in seen_ids:
                continue
            seen_ids.add(mail_id)
            subj, text = flatten_mail_text(m)
            code = extract_code(text, subj)
            print(f"[MAIL] id={mail_id} subject={subj!r} code={code!r}")
            if code:
                print(f"[FOUND] {code}")
                return
        if not mails:
            print("[INFO] no mails yet")
        time.sleep(max(args.interval, 1))
    print("[TIMEOUT] no code found")


if __name__ == "__main__":
    main()
