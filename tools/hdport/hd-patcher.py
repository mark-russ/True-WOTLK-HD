#!/usr/bin/env python3
"""
patch_wow_335a - Combined patches for stock WoW 3.3.5a (build 12340) Wow.exe

Applies both:
  1. M2 level support  – character models may use >65 535 indices (level field).
  2. Component layout  – 1024×1024 character textures with the retail face layout
                         and a fixed source-texture cache that no longer truncates
                         files ≥1 MiB.

Usage:
    python3 hd-patcher.py Wow.exe                 # patch in place (saves .bak)
    python3 hd-patcher.py Wow.exe out.exe         # write patched exe to out.exe
    python3 hd-patcher.py Wow.exe --check         # only verify that the file
                                                      # is a stock 3.3.5a client

Only the exact vanilla 3.3.5a (12340) client is accepted.  Any other build,
already-patched binary, or unexpected instruction bytes will be rejected.

After patching set in WTF/Config.wtf:
    SET componentTextureLevel "10"
(and leave componentCompress / componentThread at their defaults of "1").
Lower levels still work.  Character models/textures must use the retail1024 layout.
"""

import shutil
import struct
import sys

# ---------------------------------------------------------------------------
# Constants shared by both patches
# ---------------------------------------------------------------------------

BUILD_TIMESTAMP = 0x4C2452FE          # 3.3.5a (12340) link time, 2010-06-25
VERSION_STRING  = '3, 3, 5, 12340'.encode('utf-16le')
IMAGE_BASE      = 0x400000
M2FIX_SECTION   = b'.m2fix'

# ---------------------------------------------------------------------------
# M2-level patch sites
# (name, site VA, original hex bytes, resume VA, stub body hex)
# ---------------------------------------------------------------------------

M2_SITES = [
    ('shared index buffer build: source start', 0x83619f, '0fb75c0b08', 0x8361a4,
     '500fb7440b02c1e0100fb75c0b0809c358'),
    ('shared index buffer build: store position', 0x836240, '66895a080fb7520a', 0x836248,
     '66895a0853c1eb1066895a025b0fb7520a'),
    ('draw (batch loop): start', 0x829b54, '0fb7400883e901', 0x829b5b,
     '530fb75802c1e3100fb7400809d85b83e901'),
    ('draw (instanced): start', 0x829c5a, '0fb746088945e0', 0x829c61,
     '0fb74602c1e010668b46088945e0'),
    ('per-model index fill (cpu): start', 0x829091, '0fb74e088b5710', 0x829098,
     '0fb74e02c1e110668b4e088b5710'),
    ('per-model index fill (gpu): start', 0x8290bc, '0fb74e088b5710', 0x8290c3,
     '0fb74e02c1e110668b4e088b5710'),
    ('scene draw A: start', 0x8205dd, '0fb776088d4c01ff', 0x8205e5,
     '9c530fb75e02c1e3100fb7760809de5b9d8d4c01ff'),
    ('scene draw B: start', 0x820666, '0fb776088d4c01ff', 0x82066e,
     '9c530fb75e02c1e3100fb7760809de5b9d8d4c01ff'),
    ('scene draw C: start', 0x8206de, '0fb7560883e801', 0x8206e5,
     '0fb75602c1e210668b560883e801'),
    ('scene draw D/E: start', 0x820b9c, '0fb750088955d4', 0x820ba3,
     '9c0fb75002c1e210668b50088955d49d'),
    ('merged record: store position', 0x82cde1, '837de00066894308', 0x82cde9,
     '50c1e8106689430258837de00066894308'),
    ('geoset show/hide: section id', 0x82c7ed, '8b0410394508', 0x82c7f3,
     '0fb70410394508'),
]

# ---------------------------------------------------------------------------
# Component-layout text edits
# (VA, original bytes, new bytes, description)
# ---------------------------------------------------------------------------

TEXT_EDITS = [
    (0x4020bf, bytes.fromhex('83fe09'),       bytes.fromhex('83fe0a'),       'startup: cmp esi, 9 → 10'),
    (0x4020c6, bytes.fromhex('be09000000'),   bytes.fromhex('be0a000000'),   'startup: mov esi, 9 → 10'),
    (0x4f1a69, bytes.fromhex('83f809'),       bytes.fromhex('83f80a'),       'init: cmp eax, 9 → 10'),
    (0x4f1b4a, bytes.fromhex('b809000000'),   bytes.fromhex('b80a000000'),   'init: mov eax, 9 → 10'),
    (0x4f1b7a, bytes.fromhex('b909000000'),   bytes.fromhex('b90a000000'),   'init: table base level 9 → 10'),
    # source-texture cache: allocate full file size instead of size & 0xfffff
    (0x4f2ca0, bytes.fromhex('8b86b00000006a5d25ffff0f00'),
               bytes.fromhex('8b41089090906a5d9090909090'),
               'texture cache: allocate the full file size'),
]

HOOK_VA   = 0x9c9058
HOOK_ORIG = bytes.fromhex('890dc4b9b6005ec3')   # mov [0xb6b9c4], ecx ; pop esi ; ret
FACE_UPPER, FACE_LOWER = 0xb6b9a8, 0xb6b9b8      # master table entries 8 & 9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class PatchError(Exception):
    pass


def u16(d, o): return struct.unpack_from('<H', d, o)[0]
def u32(d, o): return struct.unpack_from('<I', d, o)[0]


def parse(data):
    if data[:2] != b'MZ':
        raise PatchError('not an exe (no MZ header)')
    pe = u32(data, 0x3C)
    if data[pe:pe + 4] != b'PE\0\0':
        raise PatchError('not a PE file')
    if u16(data, pe + 4) != 0x14C or u16(data, pe + 24) != 0x10B:
        raise PatchError('not a 32-bit Windows exe')
    opt = pe + 24
    nsec = u16(data, pe + 6)
    sec_tab = opt + u16(data, pe + 20)
    sections = []
    for i in range(nsec):
        name, vsize, va, rsize, rptr = struct.unpack_from('<8sIIII', data, sec_tab + 40 * i)
        sections.append(dict(name=name.rstrip(b'\0'), vsize=vsize, va=va,
                             rsize=rsize, rptr=rptr))
    return dict(pe=pe, opt=opt, nsec=nsec, sec_tab=sec_tab, sections=sections,
                timestamp=u32(data, pe + 8), image_base=u32(data, opt + 28),
                file_align=u32(data, opt + 36), sect_align=u32(data, opt + 32),
                headers_size=u32(data, opt + 60))


def file_offset(info, va):
    rva = va - IMAGE_BASE
    for s in info['sections']:
        if s['va'] <= rva < s['va'] + max(s['vsize'], s['rsize']):
            return s['rptr'] + (rva - s['va'])
    raise PatchError('address 0x%x is not inside any section' % va)


def find_section(info, name):
    for s in info['sections']:
        if s['name'] == name:
            return s
    return None


def pe_checksum(data, csum_off):
    total = 0
    n = len(data) - len(data) % 4
    for i in range(0, n, 4):
        if i == csum_off:
            continue
        total += u32(data, i)
        total = (total & 0xFFFFFFFF) + (total >> 32)
    if len(data) % 4:
        total += int.from_bytes(bytes(data[n:]) + bytes(4 - len(data) % 4), 'little')
        total = (total & 0xFFFFFFFF) + (total >> 32)
    total = (total & 0xFFFF) + (total >> 16)
    total += total >> 16
    return (total & 0xFFFF) + len(data)


def cave_code():
    """Build the 54-byte face-region relocation stub."""
    a  = lambda v: struct.pack('<I', v)
    st = lambda addr: b'\xa3' + a(addr)                 # mov [addr], eax
    c  = b'\xb8' + a(512)                               # mov eax, 512
    c += st(FACE_UPPER) + st(FACE_UPPER + 8)            # x,w upper
    c += st(FACE_LOWER) + st(FACE_LOWER + 8)            # x,w lower
    c += b'\xb8' + a(256)                               # mov eax, 256
    c += st(FACE_UPPER + 12)                            # h upper
    c += st(FACE_LOWER + 4) + st(FACE_LOWER + 12)       # y,h lower
    c += b'\x31\xc0' + st(FACE_UPPER + 4)               # y upper = 0
    return c + b'\x5e\xc3'                              # pop esi ; ret


# ---------------------------------------------------------------------------
# Verification (stock 3.3.5a only)
# ---------------------------------------------------------------------------

def check(data):
    """Raise PatchError unless the file is a clean stock 3.3.5a (12340) client."""
    info = parse(data)

    if any(s['name'] == M2FIX_SECTION for s in info['sections']):
        raise PatchError('already patched (.m2fix section present)')

    if info['timestamp'] != BUILD_TIMESTAMP or info['image_base'] != IMAGE_BASE:
        raise PatchError('not the 3.3.5a (12340) client: timestamp 0x%x, image base 0x%x'
                         % (info['timestamp'], info['image_base']))

    if VERSION_STRING not in data:
        raise PatchError('version resource "3, 3, 5, 12340" not found')

    # M2 sites must still contain the original instruction bytes
    bad = []
    for name, va, orig, ret, body in M2_SITES:
        off = file_offset(info, va)
        have = bytes(data[off:off + len(orig) // 2]).hex()
        if have != orig:
            bad.append('  %s @0x%x: found %s, expected %s' % (name, va, have, orig))
    if bad:
        raise PatchError('instruction bytes differ at %d of %d M2 sites '
                         '(another patch or a non-stock binary?):\n%s'
                         % (len(bad), len(M2_SITES), '\n'.join(bad)))

    # Component-layout sites must still contain the original bytes
    for va, old, new, what in TEXT_EDITS:
        off = file_offset(info, va)
        cur = bytes(data[off:off + len(old)])
        if cur != old:
            raise PatchError('unexpected bytes at 0x%x (%s): not a stock 3.3.5a 12340 exe'
                             % (va, what))

    # Hook site must still be the original epilogue
    off = file_offset(info, HOOK_VA)
    if bytes(data[off:off + len(HOOK_ORIG)]) != HOOK_ORIG:
        raise PatchError('unexpected bytes at 0x%x (table-setup epilogue)' % HOOK_VA)

    # Room for another section header
    if info['sec_tab'] + 40 * (info['nsec'] + 1) > info['headers_size']:
        raise PatchError('no room in the PE header for another section')

    return info


# ---------------------------------------------------------------------------
# Apply both patches
# ---------------------------------------------------------------------------

def patch(data):
    info = check(data)
    data = bytearray(data)
    opt, fa, sa = info['opt'], info['file_align'], info['sect_align']

    text = find_section(info, b'.text')
    if text is None:
        raise PatchError('.text section not found')

    # ------------------------------------------------------------------
    # 1. Component-layout text edits + face-region code cave
    # ------------------------------------------------------------------
    def off(va):
        return file_offset(info, va)

    code = cave_code()
    # place the cave at the end of the current .text virtual size (aligned)
    cave_va = IMAGE_BASE + text['va'] + ((text['vsize'] + 3) & ~3)
    hook = (b'\xe9' + struct.pack('<i', cave_va - (HOOK_VA + 5))
            + b'\x90' * (len(HOOK_ORIG) - 5))

    # safety: the cave must fit inside the raw .text and the area must be zero
    end_of_text = IMAGE_BASE + text['va'] + text['rsize']
    if cave_va + len(code) > end_of_text:
        raise PatchError('no free space at the end of .text for the face-region block')
    if any(data[off(cave_va):off(cave_va) + len(code)]):
        raise PatchError('non-zero bytes where the face-region block should go')

    changes = 0
    for va, old, new, what in TEXT_EDITS:
        data[off(va):off(va) + len(new)] = new
        print('  0x%06x  %s' % (va, what))
        changes += 1

    data[off(cave_va):off(cave_va) + len(code)] = code
    data[off(HOOK_VA):off(HOOK_VA) + len(hook)] = hook
    print('  0x%06x  face-region block' % cave_va)
    print('  0x%06x  jump to the face-region block' % HOOK_VA)
    changes += 2

    # raise .text VirtualSize so the cave is considered part of the section
    # (section header VirtualSize is at offset +8 inside the 40-byte entry)
    text_hdr = info['sec_tab'] + 40 * next(
        i for i, s in enumerate(info['sections']) if s['name'] == b'.text')
    new_vsize = max(text['vsize'], (cave_va - IMAGE_BASE - text['va']) + len(code))
    struct.pack_into('<I', data, text_hdr + 8, new_vsize)

    # ------------------------------------------------------------------
    # 2. M2-level stubs in a new .m2fix section
    # ------------------------------------------------------------------
    # Prefer the place where the Authenticode signature was (now invalid).
    cert_off  = u32(data, opt + 128)
    cert_size = u32(data, opt + 132)
    raw_ptr = (cert_off if cert_off and cert_off + cert_size == len(data)
               else len(data))
    raw_ptr = (raw_ptr + fa - 1) // fa * fa

    last = max(info['sections'], key=lambda s: s['va'])
    new_va = (last['va'] + max(last['vsize'], last['rsize']) + sa - 1) // sa * sa

    stub_code = bytearray()
    for name, va, orig, ret, body in M2_SITES:
        stub_va = IMAGE_BASE + new_va + len(stub_code)
        stub = bytes.fromhex(body)
        jmp_back = struct.pack('<Bi', 0xE9, ret - (stub_va + len(stub) + 5))
        stub_code += stub + jmp_back
        while len(stub_code) % 16:
            stub_code.append(0xCC)

        site_len = len(orig) // 2
        jmp = struct.pack('<Bi', 0xE9, stub_va - (va + 5))
        site_off = off(va)
        data[site_off:site_off + site_len] = jmp + b'\x90' * (site_len - 5)
        print('  0x%06x  %s' % (va, name))
        changes += 1

    raw_size = (len(stub_code) + fa - 1) // fa * fa

    # truncate any old signature / append padding + new section
    del data[raw_ptr:]
    data += bytes(raw_ptr - len(data))
    data += bytes(stub_code) + bytes(raw_size - len(stub_code))

    # write the new section header
    header = struct.pack('<8sIIIIIIHHI',
                         M2FIX_SECTION, len(stub_code), new_va, raw_size, raw_ptr,
                         0, 0, 0, 0, 0x60000020)  # code | executable | readable
    pos = info['sec_tab'] + 40 * info['nsec']
    data[pos:pos + 40] = header
    struct.pack_into('<H', data, info['pe'] + 6, info['nsec'] + 1)          # NumberOfSections
    struct.pack_into('<I', data, opt + 56,
                     new_va + (len(stub_code) + sa - 1) // sa * sa)         # SizeOfImage
    struct.pack_into('<II', data, opt + 128, 0, 0)                          # drop signature
    struct.pack_into('<I', data, opt + 64, pe_checksum(data, opt + 64))     # checksum

    return bytes(data), changes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(1)

    src = args[0]
    data = open(src, 'rb').read()

    try:
        if '--check' in sys.argv:
            check(data)
            print('OK: stock 3.3.5a (12340) exe, all patch sites match; ready to patch.')
            return

        out, n = patch(data)
    except PatchError as e:
        if '--check' in sys.argv and 'already patched' in str(e):
            print('Already patched.')
            return
        sys.exit('NOT PATCHED: %s' % e)

    dst = args[1] if len(args) > 1 else src
    if dst == src:
        shutil.copy2(src, src + '.bak')
        print('backup saved as %s.bak' % src)

    open(dst, 'wb').write(out)
    print('patched (%d changes); wrote %s' % (n, dst))
    print('Set componentTextureLevel "10" in Config.wtf.')


if __name__ == '__main__':
    main()
