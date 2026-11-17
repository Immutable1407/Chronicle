"""Mac source-run Keychain access using Apple's public C APIs.

Values are never command arguments or log messages. The OS may ask the user to
authorize the Python runtime when accessing a profile created by another app.
"""
import ctypes as C
import json
import sys


class MacKeychain:
    service = 'io.chronicle.desktop.wechat'

    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('keychain_requires_macos')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.security = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        p, n = C.c_void_p, C.c_long
        signatures = {
            'CFStringCreateWithCString': ([p, C.c_char_p, C.c_uint32], p),
            'CFDataCreate': ([p, p, n], p), 'CFDataGetLength': ([p], n), 'CFDataGetBytePtr': ([p], p),
            'CFDictionaryCreateMutable': ([p,n,p,p],p), 'CFDictionarySetValue': ([p,p,p],None),
            'CFRelease': ([p],None)}
        for name,(args,result) in signatures.items():
            function=getattr(self.cf,name);function.argtypes=args;function.restype=result
        self.security.SecItemAdd.argtypes=[p,C.POINTER(p)];self.security.SecItemAdd.restype=C.c_int32
        self.security.SecItemCopyMatching.argtypes=[p,C.POINTER(p)];self.security.SecItemCopyMatching.restype=C.c_int32
        self.security.SecItemUpdate.argtypes=[p,p];self.security.SecItemUpdate.restype=C.c_int32
        self.security.SecItemDelete.argtypes=[p];self.security.SecItemDelete.restype=C.c_int32

    def constant(self,name):
        return C.c_void_p.in_dll(self.security,name).value

    def dictionary(self,profile,extra=None):
        # Null callbacks are safe here: local values stay alive until this request ends.
        query=self.cf.CFDictionaryCreateMutable(None,0,None,None)
        owned=[query]
        def string(value):
            result=self.cf.CFStringCreateWithCString(None,value.encode(),0x08000100)
            owned.append(result);return result
        pairs={'kSecClass':self.constant('kSecClassGenericPassword'),
               'kSecAttrService':string(self.service),'kSecAttrAccount':string(profile)}
        for name,value in (extra or {}).items():pairs[name]=value
        for name,value in pairs.items():self.cf.CFDictionarySetValue(query,self.constant(name),value)
        return query,owned

    def release(self,owned):
        for value in owned:self.cf.CFRelease(value)

    def get(self,profile):
        true=C.c_void_p.in_dll(self.cf,'kCFBooleanTrue').value
        query,owned=self.dictionary(profile,{'kSecReturnData':true,'kSecMatchLimit':self.constant('kSecMatchLimitOne')})
        result=C.c_void_p()
        try:
            status=self.security.SecItemCopyMatching(query,C.byref(result))
            if status:raise RuntimeError('keychain_read_failed:%d'%status)
            value=C.string_at(self.cf.CFDataGetBytePtr(result),self.cf.CFDataGetLength(result))
            return json.loads(value)
        finally:
            if result.value:self.cf.CFRelease(result)
            self.release(owned)

    def put(self,profile,value):
        encoded=json.dumps(value,separators=(',',':')).encode()
        if len(encoded)>2*1024*1024:raise ValueError('credential_too_large')
        buffer=C.create_string_buffer(encoded)
        data=self.cf.CFDataCreate(None,buffer,len(encoded))
        query,owned=self.dictionary(profile,{'kSecValueData':data})
        try:
            status=self.security.SecItemAdd(query,None)
            if status==-25299:
                lookup,lookup_owned=self.dictionary(profile)
                attributes=self.cf.CFDictionaryCreateMutable(None,0,None,None)
                self.cf.CFDictionarySetValue(attributes,self.constant('kSecValueData'),data)
                try:status=self.security.SecItemUpdate(lookup,attributes)
                finally:self.cf.CFRelease(attributes);self.release(lookup_owned)
            if status:raise RuntimeError('keychain_write_failed:%d'%status)
        finally:self.release(owned);self.cf.CFRelease(data)

    def delete(self,profile):
        query,owned=self.dictionary(profile)
        try:
            status=self.security.SecItemDelete(query)
            if status not in (0,-25300):raise RuntimeError('keychain_delete_failed:%d'%status)
        finally:self.release(owned)
