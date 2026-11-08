"""quick structural summary of a legacy (MD20) or modern (MD21-chunked) m2"""
import struct
def info(path):
    d = open(path, 'rb').read(); chunks = {}
    if d[:4] == b'MD21':
        o = 0
        while o + 8 <= len(d):
            t = d[o:o+4].decode('latin1'); sz = struct.unpack_from('<I', d, o+4)[0]; chunks[t] = d[o+8:o+8+sz]; o += 8 + sz
        md = chunks['MD21']
    else:
        md = d
    A = lambda o: struct.unpack_from('<II', md, o)[0]
    ver = struct.unpack_from('<I', md, 4)[0]
    r = dict(ver=ver, size=len(d), flags=struct.unpack_from('<I', md, 16)[0], seq=A(28), bones=A(44), verts=A(60),
             views=struct.unpack_from('<I', md, 68)[0], tex=A(80), texanim=A(96), mats=A(112), attach=A(240),
             events=A(256), lights=A(264), cams=A(272), ribbons=A(288), particles=A(296),
             chunks=sorted(k for k in chunks if k != 'MD21'))
    # texture types/names
    if md[:4] != b'MD20' or not 256 <= ver <= 300:
        raise ValueError('not an m2 this tool understands (magic %r, version %d)' % (md[:4], ver))
    n, o = struct.unpack_from('<II', md, 80); tex = []
    if o + 16*n > len(md):
        raise ValueError('header points past the end of the file (damaged or a different format)')
    for i in range(n):
        t, f, nn, no = struct.unpack_from('<IIII', md, o+16*i)
        tex.append((t, md[no:no+nn-1].decode('latin1') if nn else ''))
    r['textures'] = tex
    nv, ov = struct.unpack_from('<II', md, 60)
    if ov + 48*nv > len(md):
        raise ValueError('vertex array runs past the end of the file')
    return r, md, chunks
