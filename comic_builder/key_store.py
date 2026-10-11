"""Remember an optional API key, protected by the operating system.

Windows: the key is encrypted with the current Windows user's DPAPI and the
encrypted value is kept in Comic Stitcher's settings.

Elsewhere: the key is kept in the desktop's keyring (GNOME Keyring, KDE Wallet
or another Secret Service provider) through the ``keyring`` package, and the
settings keep only a marker saying the key is stored there. Without a usable
keyring the option is unavailable and the key lasts until Comic Stitcher closes.
"""
import base64
import ctypes
import os

KEYRING_SERVICE = 'Comic Stitcher'
KEYRING_ACCOUNT = 'ComicVine API key'
KEYRING_MARKER = 'keyring:v1'


def _windows():
    return os.name == 'nt'


def _keyring():
    """The desktop keyring, or None when none is usable."""
    if _windows():
        return None
    try:
        import keyring
        backend = keyring.get_keyring()
        # The "fail" and "null" placeholder backends report a priority of 0 or less.
        if getattr(backend, 'priority', 0) > 0:
            return backend
    except Exception:  # A missing package or an unreachable desktop service.
        pass
    return None


def can_remember_key():
    return _windows() or _keyring() is not None


def _crypt(data, decrypt=False):
    if not _windows():
        raise ValueError('Windows protection is available on Windows only.')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = Blob(len(data), buffer)
    entropy_data = b'ComicStitcher/ComicVine'
    entropy_buffer = (ctypes.c_ubyte * len(entropy_data)).from_buffer_copy(entropy_data)
    entropy = Blob(len(entropy_data), entropy_buffer)
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                         ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]; kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(input_blob), None if decrypt else 'Comic Stitcher API key',
                    ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise ValueError('Windows could not protect or retrieve the remembered API key.')
    try:
        return ctypes.string_at(output.data, output.length)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def protect_key(key):
    """Store the key and return the value to keep in settings."""
    if _windows():
        return base64.b64encode(_crypt(key.encode('utf-8'))).decode('ascii')
    backend = _keyring()
    if backend is None:
        raise ValueError('No desktop keyring is available, so the API key cannot be remembered.')
    try:
        backend.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, key)
    except Exception as exc:  # For example, the keyring stayed locked.
        raise ValueError(f'The desktop keyring could not store the API key: {exc}') from exc
    return KEYRING_MARKER


def retrieve_key(value):
    """Return the remembered key for a settings value, or '' if it is unavailable."""
    if not value:
        return ''
    if str(value) == KEYRING_MARKER:
        backend = _keyring()
        if backend is None:
            return ''
        try:
            return backend.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or ''
        except Exception:
            return ''
    if not _windows():
        return ''
    try:
        return _crypt(base64.b64decode(str(value), validate=True), decrypt=True).decode('utf-8')
    except (ValueError, OSError, UnicodeError):
        return ''


def forget_key(value):
    """Remove a key kept outside the settings file (the desktop keyring)."""
    if str(value or '') != KEYRING_MARKER:
        return  # Windows keeps its encrypted value in settings; removing that is enough.
    backend = _keyring()
    if backend is None:
        return
    try:
        backend.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:  # Already gone, or the keyring is unavailable.
        pass
