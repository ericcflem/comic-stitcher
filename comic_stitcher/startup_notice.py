"""Tell an X11 desktop that Comic Stitcher has finished starting.

When an app is started from the app menu, the desktop shows a busy cursor until
the app says it is ready, or until a timeout (about 15 seconds in GNOME). The
desktop passes an ID in DESKTOP_STARTUP_ID; on X11, including XWayland, the app
ends the busy cursor by sending a "remove" message with that ID (the
freedesktop.org startup-notification protocol). Qt doesn't send it for plain Qt
apps, so this does, through libxcb, without needing Qt.

Nothing here imports Qt, and every failure is silent: the worst case is the
desktop's own timeout, as before.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os

VARIABLE = "DESKTOP_STARTUP_ID"
CHUNK = 20  # Bytes of message text in each X client message.
CLIENT_MESSAGE = 33
EVENT_MASK_PROPERTY_CHANGE = 1 << 22


def take_startup_id(environ=None) -> str | None:
    """The desktop's startup ID, removed from the environment.

    Removing it keeps programs Comic Stitcher starts later (such as the drop tab)
    from reusing an ID that belongs to this launch.
    """
    environ = os.environ if environ is None else environ
    value = environ.pop(VARIABLE, None)
    return value or None


def _quoted(value: str) -> str:
    if any(character in value for character in ' "\\'):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def remove_message(startup_id: str) -> bytes:
    """The protocol's message ending the startup, NUL-terminated."""
    return f"remove: ID={_quoted(startup_id)}".encode() + b"\0"


def chunks(message: bytes) -> list[bytes]:
    """The message split into the 20-byte pieces each client message carries."""
    return [message[start:start + CHUNK].ljust(CHUNK, b"\0") for start in range(0, len(message), CHUNK)]


class _Cookie(ctypes.Structure):
    _fields_ = [("sequence", ctypes.c_uint)]


class _AtomReply(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("pad0", ctypes.c_uint8), ("sequence", ctypes.c_uint16),
                ("length", ctypes.c_uint32), ("atom", ctypes.c_uint32)]


class _ScreenIterator(ctypes.Structure):
    _fields_ = [("data", ctypes.POINTER(ctypes.c_uint32)), ("rem", ctypes.c_int), ("index", ctypes.c_int)]


class _ClientMessage(ctypes.Structure):
    _fields_ = [("response_type", ctypes.c_uint8), ("format", ctypes.c_uint8), ("sequence", ctypes.c_uint16),
                ("window", ctypes.c_uint32), ("type", ctypes.c_uint32), ("data", ctypes.c_char * CHUNK)]


def _xcb():
    library = ctypes.CDLL(ctypes.util.find_library("xcb") or "libxcb.so.1")
    library.xcb_connect.restype = ctypes.c_void_p
    library.xcb_connect.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
    library.xcb_connection_has_error.argtypes = [ctypes.c_void_p]
    library.xcb_get_setup.restype = ctypes.c_void_p
    library.xcb_get_setup.argtypes = [ctypes.c_void_p]
    library.xcb_setup_roots_iterator.restype = _ScreenIterator
    library.xcb_setup_roots_iterator.argtypes = [ctypes.c_void_p]
    library.xcb_intern_atom.restype = _Cookie
    library.xcb_intern_atom.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_char_p]
    library.xcb_intern_atom_reply.restype = ctypes.POINTER(_AtomReply)
    library.xcb_intern_atom_reply.argtypes = [ctypes.c_void_p, _Cookie, ctypes.c_void_p]
    library.xcb_generate_id.restype = ctypes.c_uint32
    library.xcb_generate_id.argtypes = [ctypes.c_void_p]
    library.xcb_create_window.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint32,
                                          ctypes.c_int16, ctypes.c_int16, ctypes.c_uint16, ctypes.c_uint16,
                                          ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint32,
                                          ctypes.c_void_p]
    library.xcb_send_event.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.c_void_p]
    library.xcb_destroy_window.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    library.xcb_flush.argtypes = [ctypes.c_void_p]
    library.xcb_get_input_focus.restype = _Cookie
    library.xcb_get_input_focus.argtypes = [ctypes.c_void_p]
    library.xcb_get_input_focus_reply.restype = ctypes.c_void_p
    library.xcb_get_input_focus_reply.argtypes = [ctypes.c_void_p, _Cookie, ctypes.c_void_p]
    library.xcb_disconnect.argtypes = [ctypes.c_void_p]
    return library


def _atom(xcb, connection, name: bytes) -> int:
    reply = xcb.xcb_intern_atom_reply(connection, xcb.xcb_intern_atom(connection, 0, len(name), name), None)
    if not reply:
        raise OSError("X server did not answer")
    try:
        return reply.contents.atom
    finally:
        ctypes.CDLL(ctypes.util.find_library("c")).free(reply)


def send_remove(startup_id: str, display: str | None = None) -> bool:
    """Send the "remove" message on the X display. True if it was sent."""
    try:
        xcb = _xcb()
        screen = ctypes.c_int(0)
        connection = xcb.xcb_connect(display.encode() if display else None, ctypes.byref(screen))
        if not connection:
            return False
        try:
            if xcb.xcb_connection_has_error(connection):
                return False
            roots = xcb.xcb_setup_roots_iterator(xcb.xcb_get_setup(connection))
            if not roots.data or roots.rem < 1:
                return False
            root = roots.data[0]  # xcb_screen_t begins with its root window.
            begin = _atom(xcb, connection, b"_NET_STARTUP_INFO_BEGIN")
            more = _atom(xcb, connection, b"_NET_STARTUP_INFO")
            # The protocol sends from a window of the sender's own.
            sender = xcb.xcb_generate_id(connection)
            xcb.xcb_create_window(connection, 0, sender, root, -100, -100, 1, 1, 0, 2, 0, 0, None)
            for number, piece in enumerate(chunks(remove_message(startup_id))):
                event = _ClientMessage(CLIENT_MESSAGE, 8, 0, sender, begin if number == 0 else more, piece)
                xcb.xcb_send_event(connection, 0, root, EVENT_MASK_PROPERTY_CHANGE, ctypes.byref(event))
            xcb.xcb_destroy_window(connection, sender)
            # Wait until the X server has handled everything before disconnecting.
            reply = xcb.xcb_get_input_focus_reply(connection, xcb.xcb_get_input_focus(connection), None)
            if reply:
                ctypes.CDLL(ctypes.util.find_library("c")).free(ctypes.c_void_p(reply))
            return True
        finally:
            xcb.xcb_disconnect(connection)
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def finish_startup(startup_id: str | None, platform_name: str) -> bool:
    """End the desktop's busy cursor for this launch, on X11 or XWayland."""
    if not startup_id or platform_name != "xcb":
        return False
    return send_remove(startup_id)
