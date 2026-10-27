"""Ending the desktop's busy cursor when Comic Stitcher starts from the app menu."""
import ctypes
import os
import shutil
import subprocess
import sys
import time
import unittest
import unittest.mock

from . import startup_notice
from .startup_notice import _xcb, chunks, finish_startup, remove_message, send_remove, take_startup_id

GNOME_ID = 'gnome-shell/Comic Stitcher/4321-2-host_TIME98765'


class MessageTests(unittest.TestCase):
    def test_id_is_taken_from_the_environment(self):
        environ = {"DESKTOP_STARTUP_ID": "abc", "OTHER": "1"}
        self.assertEqual(take_startup_id(environ), "abc")
        self.assertNotIn("DESKTOP_STARTUP_ID", environ)  # Not passed on to programs started later.
        self.assertIsNone(take_startup_id({"DESKTOP_STARTUP_ID": ""}))

    def test_message_quotes_ids_with_spaces_and_fits_client_messages(self):
        self.assertEqual(remove_message("simple_TIME1"), b"remove: ID=simple_TIME1\0")
        message = remove_message('a "b" c\\d')
        self.assertEqual(message, b'remove: ID="a \\"b\\" c\\\\d"\0')
        pieces = chunks(remove_message(GNOME_ID))
        self.assertTrue(all(len(piece) == 20 for piece in pieces))
        self.assertEqual(b"".join(pieces).rstrip(b"\0") + b"\0", remove_message(GNOME_ID))

    def test_nothing_is_sent_without_an_id_or_off_x11(self):
        with unittest.mock.patch.object(startup_notice, "send_remove") as send:
            self.assertFalse(finish_startup(None, "xcb"))
            self.assertFalse(finish_startup("id", "wayland"))
            send.assert_not_called()

    def test_a_missing_display_is_harmless(self):
        self.assertFalse(send_remove("id", display=":987"))


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("Xvfb"), "needs Xvfb")
class DesktopReceivesTests(unittest.TestCase):
    """A listener on a virtual X display, as a desktop listens, gets the whole message."""

    def setUp(self):
        self.display = ":71"
        self.server = subprocess.Popen(["Xvfb", self.display, "-nolisten", "tcp"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.server.wait, 5)
        self.addCleanup(self.server.terminate)
        deadline = time.monotonic() + 10
        while not os.path.exists(f"/tmp/.X11-unix/X{self.display[1:]}") and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_remove_message_arrives_whole(self):
        xcb = _xcb()
        xcb.xcb_change_window_attributes.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                                     ctypes.POINTER(ctypes.c_uint32)]
        xcb.xcb_poll_for_event.restype = ctypes.POINTER(startup_notice._ClientMessage)
        xcb.xcb_poll_for_event.argtypes = [ctypes.c_void_p]
        listener = xcb.xcb_connect(self.display.encode(), None)
        self.addCleanup(xcb.xcb_disconnect, listener)
        self.assertFalse(xcb.xcb_connection_has_error(listener))
        root = xcb.xcb_setup_roots_iterator(xcb.xcb_get_setup(listener)).data[0]
        mask = (ctypes.c_uint32 * 1)(startup_notice.EVENT_MASK_PROPERTY_CHANGE)
        xcb.xcb_change_window_attributes(listener, root, 0x800, mask)  # XCB_CW_EVENT_MASK
        xcb.xcb_flush(listener)
        begin = startup_notice._atom(xcb, listener, b"_NET_STARTUP_INFO_BEGIN")
        more = startup_notice._atom(xcb, listener, b"_NET_STARTUP_INFO")

        self.assertTrue(send_remove(GNOME_ID, display=self.display))

        received, types = b"", []
        deadline = time.monotonic() + 5
        while b"\0" not in received and time.monotonic() < deadline:
            event = xcb.xcb_poll_for_event(listener)
            if not event:
                time.sleep(0.02)
                continue
            if event.contents.response_type & 0x7F == startup_notice.CLIENT_MESSAGE:
                types.append(event.contents.type)
                received += bytes(event.contents.data)
        self.assertEqual(received.split(b"\0")[0] + b"\0", remove_message(GNOME_ID))
        self.assertEqual(types[0], begin)
        self.assertTrue(all(kind == more for kind in types[1:]))


if __name__ == "__main__":
    unittest.main()
