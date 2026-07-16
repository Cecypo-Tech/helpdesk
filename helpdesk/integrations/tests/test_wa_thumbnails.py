import io
import unittest

import frappe

from helpdesk.integrations.wa import (
    _THUMB_MAX_PX,
    _buffer_to_bytes,
    _downscale_image,
    _video_poster_from_raw,
    thumbnail_for_media_url,
)


def _jpeg_bytes(size=(1600, 1200)):
    """A photo-ish JPEG big enough that downscaling is worthwhile."""
    from PIL import Image

    img = Image.new("RGB", size)
    # Gradient rather than flat colour: a solid image compresses so well that
    # the thumbnail wouldn't be smaller, which is a legitimate skip case.
    px = img.load()
    for x in range(0, size[0], 4):
        for y in range(0, size[1], 4):
            for dx in range(4):
                for dy in range(4):
                    if x + dx < size[0] and y + dy < size[1]:
                        px[x + dx, y + dy] = ((x * 7) % 256, (y * 13) % 256, ((x + y) * 3) % 256)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    return out.getvalue()


class TestWaBufferDecoding(unittest.TestCase):
    """The provider serialises binary as JSON, and not as base64.

    Observed on real payloads: a numeric-keyed object ({"0": 255, "1": 216, ...}).
    Getting this wrong silently costs every video its poster.
    """

    JPEG_MAGIC = [255, 216, 255, 224]

    def test_numeric_keyed_buffer(self):
        value = {str(i): b for i, b in enumerate(self.JPEG_MAGIC)}
        self.assertEqual(bytes(self.JPEG_MAGIC), _buffer_to_bytes(value))

    def test_numeric_keys_decode_in_index_order_not_dict_order(self):
        shuffled = {"2": 255, "0": 255, "3": 224, "1": 216}
        self.assertEqual(bytes(self.JPEG_MAGIC), _buffer_to_bytes(shuffled))

    def test_node_buffer_dict(self):
        self.assertEqual(
            bytes(self.JPEG_MAGIC),
            _buffer_to_bytes({"type": "Buffer", "data": self.JPEG_MAGIC}),
        )

    def test_plain_list(self):
        self.assertEqual(bytes(self.JPEG_MAGIC), _buffer_to_bytes(self.JPEG_MAGIC))

    def test_base64_string(self):
        import base64

        encoded = base64.b64encode(bytes(self.JPEG_MAGIC)).decode()
        self.assertEqual(bytes(self.JPEG_MAGIC), _buffer_to_bytes(encoded))

    def test_bad_input_is_falsy_rather_than_raising(self):
        """Ingest must never fail because a preview frame was malformed.

        Callers test truthiness (`if not content: return ""`), so empty bytes and
        None are equally fine — the contract is "no usable preview", not None.
        """
        for value in (None, {}, [], "!!!not base64!!!", {"nope": 1}, 42):
            self.assertFalse(_buffer_to_bytes(value), f"expected falsy for {value!r}")


class TestWaDownscale(unittest.TestCase):
    def test_downscales_within_bounds_and_shrinks(self):
        original = _jpeg_bytes()
        small = _downscale_image(original, "jpg")
        self.assertIsNotNone(small)
        self.assertLess(len(small), len(original))

        from PIL import Image

        img = Image.open(io.BytesIO(small))
        self.assertLessEqual(max(img.size), _THUMB_MAX_PX)

    def test_gif_is_skipped_to_preserve_animation(self):
        """A static JPEG of frame 1 would silently kill an animated GIF."""
        self.assertIsNone(_downscale_image(_jpeg_bytes(), "gif"))

    def test_returns_none_when_not_worth_it(self):
        """An image already compressed harder than we'd re-encode it must be left alone.

        Re-encoding it at q75 would produce a *bigger* file, so a thumbnail would
        cost an extra request and save nothing. Sized so pixel data dominates:
        below ~100px the JPEG header dominates and re-encoding always "shrinks",
        which is why this needs a mid-size image rather than a tiny one.
        """
        from PIL import Image

        img = Image.new("RGB", (200, 150))
        px = img.load()
        for x in range(200):
            for y in range(150):
                px[x, y] = ((x * 7) % 256, (y * 13) % 256, ((x + y) * 3) % 256)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=30)  # already below our q75
        self.assertIsNone(_downscale_image(out.getvalue(), "jpg"))

    def test_corrupt_bytes_return_none_rather_than_raising(self):
        self.assertIsNone(_downscale_image(b"this is not an image", "jpg"))


class TestWaThumbnailForMediaUrl(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self._files = []

    def tearDown(self):
        for name in self._files:
            frappe.db.delete("File", {"name": name})
        frappe.db.commit()

    def _upload(self, content, filename):
        doc = frappe.get_doc({
            "doctype": "File",
            "file_name": filename,
            "content": content,
            "is_private": 0,
        }).insert(ignore_permissions=True)
        self._files.append(doc.name)
        return doc.file_url

    def test_generates_thumbnail_for_stored_image(self):
        url = self._upload(_jpeg_bytes(), f"_test_wa_{frappe.generate_hash(length=6)}.jpg")
        thumb_url = thumbnail_for_media_url(url)
        self.assertTrue(thumb_url, "expected a thumbnail for a large stored image")

        thumb_name = frappe.db.get_value("File", {"file_url": thumb_url}, "name")
        self.assertTrue(thumb_name, "thumbnail File record was not created")
        self._files.append(thumb_name)
        self.assertNotEqual(url, thumb_url)

    def test_non_image_returns_empty(self):
        """Callers fall back to media_url on "" — videos must not go down this path."""
        self.assertEqual("", thumbnail_for_media_url("/files/whatever.mp4"))
        self.assertEqual("", thumbnail_for_media_url("/files/whatever.pdf"))

    def test_missing_file_returns_empty(self):
        self.assertEqual("", thumbnail_for_media_url("/files/_test_does_not_exist_xyz.jpg"))

    def test_empty_url_returns_empty(self):
        self.assertEqual("", thumbnail_for_media_url(""))
        self.assertEqual("", thumbnail_for_media_url(None))


class TestWaVideoPoster(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def test_no_preview_frame_returns_empty(self):
        """Historical videos have no bundled frame; that must be a quiet no-poster."""
        self.assertEqual("", _video_poster_from_raw({}))
        self.assertEqual("", _video_poster_from_raw({"videoMessage": {}}))

    def test_image_message_is_not_treated_as_a_video(self):
        # Images get a real Pillow thumbnail instead of the tiny preview frame.
        raw = {"imageMessage": {"jpegThumbnail": {"0": 255, "1": 216}}}
        self.assertEqual("", _video_poster_from_raw(raw))

    def test_preview_frame_is_stored_as_poster(self):
        poster = _jpeg_bytes(size=(200, 150))
        raw = {"videoMessage": {"jpegThumbnail": {str(i): b for i, b in enumerate(poster)}}}
        url = _video_poster_from_raw(raw)
        self.assertTrue(url, "a bundled preview frame should become a poster")

        name = frappe.db.get_value("File", {"file_url": url}, "name")
        self.assertTrue(name)
        frappe.db.delete("File", {"name": name})
        frappe.db.commit()
