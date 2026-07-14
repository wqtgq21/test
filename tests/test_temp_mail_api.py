import unittest
from unittest.mock import patch

import grok_register_ttk as app


class DummyResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TempMailTests(unittest.TestCase):
    original_config = app.DEFAULT_CONFIG.copy()

    def setUp(self):
        self.original_config = app.config.copy()
        app.config = app.DEFAULT_CONFIG.copy()

    def tearDown(self):
        app.config = self.original_config

    def test_default_config_points_at_new_mail_api(self):
        self.assertEqual(app.DEFAULT_CONFIG["mail_api_url"], "https://mail.minecraft-cn.net")
        self.assertEqual(app.DEFAULT_CONFIG["mail_domain"], "olsbvgq.shop")
        # 旧邮箱配置应已彻底移除
        for stale in (
            "email_provider",
            "duckmail_api_key",
            "cloudflare_api_base",
            "cloudflare_api_key",
            "cloudflare_auth_mode",
            "cloudflare_custom_auth",
            "cloudflare_path_domains",
            "cloudflare_path_accounts",
            "cloudflare_path_token",
            "cloudflare_path_messages",
            "defaultDomains",
            "yyds_api_key",
            "yyds_jwt",
        ):
            self.assertNotIn(stale, app.DEFAULT_CONFIG, f"DEFAULT_CONFIG 不应再含 {stale}")

    def test_create_temp_address_posts_username_and_domain(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return DummyResponse(
                {"email": "u_abcdef123456@olsbvgq.shop", "token": "tok-123"}
            )

        with patch.object(app, "http_post", side_effect=fake_post):
            email, token = app.mail_create_temp_address()

        self.assertEqual(email, "u_abcdef123456@olsbvgq.shop")
        self.assertEqual(token, "tok-123")
        self.assertEqual(captured["url"], "https://mail.minecraft-cn.net/api/v1/addresses")
        self.assertIn("username", captured["json"])
        self.assertEqual(captured["json"]["domain"], "olsbvgq.shop")
        self.assertTrue(captured["json"]["username"].startswith("u_"))

    def test_create_temp_address_raises_without_email_or_token(self):
        with patch.object(app, "http_post", return_value=DummyResponse({"email": ""})):
            self.assertRaises(Exception, app.mail_create_temp_address)

    def test_get_inbox_returns_emails_list(self):
        with patch.object(
            app,
            "http_get",
            return_value=DummyResponse(
                {"emails": [{"id": "m1", "subject": "xAI ABC-123", "body": "<p>hi</p>"}]}
            ),
        ) as m:
            mails = app.mail_get_inbox("tok-123")

        self.assertEqual(len(mails), 1)
        self.assertEqual(mails[0]["id"], "m1")
        called_url = m.call_args[0][0]
        self.assertEqual(called_url, "https://mail.minecraft-cn.net/api/v1/tok-123/emails")

    def test_get_inbox_handles_bare_list_response(self):
        with patch.object(
            app,
            "http_get",
            return_value=DummyResponse([{"id": "m1", "subject": "x"}]),
        ):
            mails = app.mail_get_inbox("tok-123")
        self.assertEqual(len(mails), 1)

    def test_extract_verification_code_prefers_grok_dash_format(self):
        self.assertEqual(
            app.extract_verification_code("", "Your code ABC-123 xAI"),
            "ABC-123",
        )
        self.assertEqual(app.extract_verification_code("code XYZ-9X1", ""), "XYZ-9X1")

    def test_extract_verification_code_falls_back_to_digits(self):
        self.assertEqual(
            app.extract_verification_code("your code: 482619", ""),
            "482619",
        )

    def test_get_email_and_token_returns_pair(self):
        with patch.object(app, "mail_create_temp_address", return_value=("a@b.shop", "tok")):
            email, token = app.get_email_and_token()
        self.assertEqual(email, "a@b.shop")
        self.assertEqual(token, "tok")


if __name__ == "__main__":
    unittest.main()
