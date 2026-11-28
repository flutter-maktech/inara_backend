import os
import sys
import unittest
import httpx

backend_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(backend_dir)
sys.path.insert(0, backend_dir)

from app.main import app
from app.core.config import settings
from app.core.notification_service import sanitize_phone_number, _extract_template_body


class TestWhatsAppIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_settings_configured(self):
        self.assertEqual(settings.WHATSAPP_PHONE_NUMBER_ID, "1255902564273221")
        self.assertEqual(settings.WHATSAPP_API_VERSION, "v19.0")
        self.assertEqual(settings.WHATSAPP_BUSINESS_ACCOUNT_ID, "2022478045055062")
        self.assertEqual(settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN, "inara_waba_verify_2026")
        self.assertEqual(settings.WHATSAPP_DEFAULT_TEMPLATE_NAME, "inara_member_message")
        self.assertEqual(settings.WHATSAPP_DEFAULT_TEMPLATE_LANG, "en")
        self.assertTrue(len(settings.WHATSAPP_ACCESS_TOKEN) > 50)

    async def test_phone_sanitization(self):
        # Local Qatar numbers (8 digits) -> prepends 974
        self.assertEqual(sanitize_phone_number("52013171"), "97452013171")
        self.assertEqual(sanitize_phone_number("33123456"), "97433123456")

        # Full international with formatting symbols
        self.assertEqual(sanitize_phone_number("+974 5201 3171"), "97452013171")
        self.assertEqual(sanitize_phone_number("+974-5201-3171"), "97452013171")
        self.assertEqual(sanitize_phone_number("00974 5201 3171"), "97452013171")

        # Other country code
        self.assertEqual(sanitize_phone_number("+1 (555) 123-4567"), "15551234567")

    async def test_extract_template_body(self):
        # Plain message should remain untouched
        msg = "Tomorrow's class will start at 10 AM."
        self.assertEqual(_extract_template_body(msg), msg)

        # Full template pasted by mistake should have inner message extracted
        full_paste = (
            "Hello {{1}}, a message from Inara Yoga.\n\n"
            "Our December schedule is now live!\n\n"
            "If you have any questions, simply reply to this message. We look forward to seeing you at the studio."
        )
        self.assertEqual(_extract_template_body(full_paste), "Our December schedule is now live!")

    async def test_webhook_verification_success(self):
        challenge = "random_challenge_xyz_12345"
        resp = await self.client.get(
            "/api/v1/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "inara_waba_verify_2026",
                "hub.challenge": challenge,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, challenge)

    async def test_webhook_verification_failure(self):
        resp = await self.client.get(
            "/api/v1/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "test",
            },
        )
        self.assertEqual(resp.status_code, 403)

    async def test_webhook_event_post(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "2022478045055062",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "+974 5201 3171",
                                    "phone_number_id": "1255902564273221"
                                },
                                "statuses": [
                                    {
                                        "id": "wamid.HBgL...",
                                        "status": "delivered",
                                        "timestamp": "1710000000",
                                        "recipient_id": "97452013171"
                                    }
                                ]
                            },
                            "field": "messages"
                        }
                    ]
                }
            ]
        }
        resp = await self.client.post("/api/v1/whatsapp/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
