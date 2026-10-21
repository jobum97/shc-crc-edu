"""프로젝트의 Pretendard를 현재 macOS 로그인 세션에 등록함. 영구 설치 없음."""
import ctypes as c
import json
from pathlib import Path

cf=c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
ct=c.CDLL('/System/Library/Frameworks/CoreText.framework/CoreText')
cf.CFURLCreateFromFileSystemRepresentation.argtypes=[c.c_void_p,c.c_char_p,c.c_long,c.c_bool]
cf.CFURLCreateFromFileSystemRepresentation.restype=c.c_void_p
cf.CFRelease.argtypes=[c.c_void_p]
cf.CFErrorGetCode.argtypes=[c.c_void_p]; cf.CFErrorGetCode.restype=c.c_long
ct.CTFontManagerRegisterFontsForURL.argtypes=[c.c_void_p,c.c_uint32,c.POINTER(c.c_void_p)]
ct.CTFontManagerRegisterFontsForURL.restype=c.c_bool
results=[]
for path in sorted((Path(__file__).resolve().parent/'assets/fonts').glob('*.otf')):
    raw=str(path).encode();url=cf.CFURLCreateFromFileSystemRepresentation(None,raw,len(raw),False)
    err=c.c_void_p()
    ok=ct.CTFontManagerRegisterFontsForURL(url,3,c.byref(err))
    code=cf.CFErrorGetCode(err) if err else None
    results.append({'font':path.name,'registered':bool(ok),'error_code':code})
    if err: cf.CFRelease(err)
    cf.CFRelease(url)
print(json.dumps(results))
if not results or any(not r['registered'] and r['error_code']!=105 for r in results):
    raise SystemExit(1)
