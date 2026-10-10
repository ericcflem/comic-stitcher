"""Protect an optional remembered API key with the current Windows user's DPAPI."""
import base64
import ctypes
import os


def can_remember_key():
    return os.name == 'nt'


def _crypt(data, decrypt=False):
    if not can_remember_key():
        raise ValueError('Remembering an API key is available on Windows only.')
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
    return base64.b64encode(_crypt(key.encode('utf-8'))).decode('ascii')


def retrieve_key(value):
    if not value or not can_remember_key(): return ''
    try:
        return _crypt(base64.b64decode(str(value), validate=True), decrypt=True).decode('utf-8')
    except (ValueError, OSError, UnicodeError):
        return ''
