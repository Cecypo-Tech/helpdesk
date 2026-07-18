import unittest

from helpdesk.integrations.wa import _extract_reply_target


class TestExtractReplyTarget(unittest.TestCase):
    """Locating the quoted-message id across the shapes Evolution actually sends.

    The payloads below are real webhook captures. The load-bearing case is the
    plain-text reply: Evolution v2 delivers it as a bare `conversation` and puts
    the quote at the TOP-LEVEL `data.contextInfo`, not inside `message`. Missing
    that is why text-reply quote boxes never rendered in the chat.
    """

    def test_plain_text_reply_uses_top_level_context_info(self):
        # Captured live: long-press "Brioche bread" -> Reply -> "Quote test gamma".
        data = {
            "key": {"id": "ACAEB7875A6D06EA81558E9B3E6BCD39", "fromMe": True,
                    "remoteJid": "254725646926@s.whatsapp.net"},
            "message": {"conversation": "Quote test gamma"},
            "messageType": "conversation",
            "contextInfo": {
                "participant": "254725646926@s.whatsapp.net",
                "quotedMessage": {"conversation": "Brioche bread"},
                "stanzaId": "ACFB212CF299F5146132274A5A84278E",
            },
        }
        self.assertEqual(
            "ACFB212CF299F5146132274A5A84278E",
            _extract_reply_target(data, data["message"], "text"),
        )

    def test_plain_text_without_reply_returns_empty(self):
        data = {"message": {"conversation": "just a normal message"}}
        self.assertEqual("", _extract_reply_target(data, data["message"], "text"))

    def test_media_reply_uses_nested_context_info(self):
        # Media replies keep the quote inside the media sub-message.
        raw_msg = {
            "imageMessage": {
                "caption": "see this",
                "contextInfo": {"stanzaId": "AC6DF778DF48E56722157A68942A5F86"},
            }
        }
        data = {"message": raw_msg}
        self.assertEqual(
            "AC6DF778DF48E56722157A68942A5F86",
            _extract_reply_target(data, raw_msg, "image"),
        )

    def test_extended_text_reply_uses_nested_context_info(self):
        raw_msg = {
            "extendedTextMessage": {
                "text": "replying",
                "contextInfo": {"stanzaId": "3EB04033C2C0A5CDFCAA1B"},
            }
        }
        data = {"message": raw_msg}
        self.assertEqual(
            "3EB04033C2C0A5CDFCAA1B",
            _extract_reply_target(data, raw_msg, "text"),
        )

    def test_nested_context_info_wins_over_top_level(self):
        # A message that carries both keeps the message-scoped quote.
        raw_msg = {"extendedTextMessage": {"text": "x", "contextInfo": {"stanzaId": "NESTED"}}}
        data = {"message": raw_msg, "contextInfo": {"stanzaId": "TOPLEVEL"}}
        self.assertEqual("NESTED", _extract_reply_target(data, raw_msg, "text"))

    def test_reaction_uses_reaction_key_id(self):
        raw_msg = {"reactionMessage": {"key": {"id": "TARGET_MSG_ID"}, "text": "👍"}}
        data = {"message": raw_msg}
        self.assertEqual(
            "TARGET_MSG_ID",
            _extract_reply_target(data, raw_msg, "reaction"),
        )

    def test_reaction_without_key_returns_empty(self):
        raw_msg = {"reactionMessage": {"text": ""}}
        data = {"message": raw_msg}
        self.assertEqual("", _extract_reply_target(data, raw_msg, "reaction"))

    def test_context_info_without_stanza_id_returns_empty(self):
        # WhatsApp status/broadcast images carry a contextInfo with no stanzaId.
        data = {
            "message": {"imageMessage": {"contextInfo": {"statusAttributions": []}}},
        }
        self.assertEqual("", _extract_reply_target(data, data["message"], "image"))
