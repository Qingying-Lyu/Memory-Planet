"""Windows current-user encryption. Never write a plaintext credential."""
import base64,ctypes,os
from ctypes import wintypes
PREFIX=b'MP-DPAPI-1:'
class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
def crypt(raw,decrypt=False):
    if os.name!='nt':raise ValueError('凭据保护需要Windows')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    result=Blob();dll=ctypes.WinDLL('crypt32',use_last_error=True)
    if decrypt:
        fn=dll.CryptUnprotectData
        fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok=fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result))
    else:
        fn=dll.CryptProtectData
        fn.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok=fn(ctypes.byref(source),'Memory Planet',None,None,None,1,ctypes.byref(result))
    if not ok:raise ValueError('凭据无法读取，请在配置页面重新填写')
    try:return ctypes.string_at(result.data,result.size)
    finally:
        free=ctypes.WinDLL('kernel32').LocalFree
        free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
        free(ctypes.cast(result.data,ctypes.c_void_p))
def seal(value):return PREFIX+base64.b64encode(crypt(value.encode('utf-8')))
def unseal(raw):return crypt(base64.b64decode(raw[len(PREFIX):],validate=True),True).decode('utf-8')
