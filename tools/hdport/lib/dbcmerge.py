#!/usr/bin/env python3
"""
Replace the customization rows of every race in rows.json inside 3.3.5a DBCs.

  python3 dbcmerge.py <dbc_in_dir> <dbc_out_dir> rows.json

Touches: CharSections.dbc, CharHairGeosets.dbc, CharacterFacialHairStyles.dbc, BarberShopStyle.dbc
Every other row (other races) is kept byte-for-byte. Copy the output DBCs to BOTH the client
patch (DBFilesClient\\) and the server's data/dbc folder - the server validates appearance with them.
"""
import struct, json, os, sys, shutil

class DBC:
    def __init__(s, path):
        d = open(path, 'rb').read(); assert d[:4] == b'WDBC', path
        s.n, s.nf, s.rs, s.ss = struct.unpack_from('<4I', d, 4)
        assert s.rs == s.nf * 4
        s.rows = [list(struct.unpack_from('<%dI' % s.nf, d, 20 + i*s.rs)) for i in range(s.n)]
        s.strings = d[20 + s.n*s.rs:]
    def str(s, o):
        return s.strings[o:s.strings.index(b'\0', o)].decode('latin1')

def write(path, nf, rows, strfields):
    sb = bytearray(b'\0'); cache = {'': 0}; out = bytearray()
    for r in rows:
        vals = []
        for i, v in enumerate(r):
            if i in strfields:
                if v not in cache:
                    cache[v] = len(sb); sb += v.encode('latin1') + b'\0'
                vals.append(cache[v])
            elif isinstance(v, float):
                vals.append(struct.unpack('<I', struct.pack('<f', v))[0])
            else:
                vals.append(v & 0xFFFFFFFF)
        out += struct.pack('<%dI' % nf, *vals)
    with open(path, 'wb') as f:
        f.write(b'WDBC' + struct.pack('<4I', len(rows), nf, nf*4, len(sb)) + out + sb)

def strings_of(db, strfields):
    """decode string fields so rows can be re-written with a fresh string block"""
    return [[db.str(v) if i in strfields else v for i, v in enumerate(r)] for r in db.rows]

def merge(src, dst, allrows):
    """allrows: {race_id: {sexname: {'sex': 0|1, 'rows': {...}}}} - every listed race's rows are replaced."""
    os.makedirs(dst, exist_ok=True)
    races = {int(r) for r in allrows}
    notes = []
    def find(name):
        for f in os.listdir(src):
            if f.lower() == name.lower(): return os.path.join(src, f)
        raise SystemExit('missing ' + name + ' in ' + src)
    def blocks():
        for race, sexes in sorted(allrows.items(), key=lambda kv: int(kv[0])):
            for sexname, blk in sexes.items():
                yield int(race), blk

    # ---------------- CharSections: ID, Race, Sex, Section, Tex1, Tex2, Tex3, Flags, Variation, Color
    db = DBC(find('CharSections.dbc')); S = {4, 5, 6}
    old = strings_of(db, S)
    ids = [r[0] for r in old if r[1] in races]; kept = [r for r in old if r[1] not in races]
    nid = max(r[0] for r in old) + 1; new = []
    sect = {'skin': 0, 'face': 1, 'facial': 2, 'hair': 3, 'underwear': 4}
    for race, blk in blocks():
        for kind, t in sect.items():
            for var, col, a, b, c, flags in blk['rows'][kind]:
                rid = ids.pop(0) if ids else None
                if rid is None: rid = nid; nid += 1
                new.append([rid, race, blk['sex'], t, a, b, c, flags, var, col])
    write(os.path.join(dst, 'CharSections.dbc'), db.nf, sorted(kept + new, key=lambda r: r[0]), S)
    # how many choices each slider had before vs now: existing characters / NPCs (CreatureDisplayInfoExtra)
    # store indices, so a shrinking or reordered list changes how they look
    names = {0: 'skin colours', 1: 'faces', 3: 'hair colours'}
    for race, blk in blocks():
        for t, what in names.items():
            col = 8 if t == 1 else 9
            before = {r[col] for r in old if r[1] == race and r[2] == blk['sex'] and r[3] == t}
            after = {r[col] for r in new if r[1] == race and r[2] == blk['sex'] and r[3] == t}
            if before and len(before) != len(after):
                notes.append('race %d sex %d: %s %d -> %d' % (race, blk['sex'], what, len(before), len(after)))
    print('CharSections: %d rows for races %s' % (len(new), sorted(races)))

    # ---------------- CharHairGeosets: ID, Race, Sex, Variation, GeosetID, Showscalp
    db = DBC(find('CharHairGeosets.dbc'))
    ids = [r[0] for r in db.rows if r[1] in races]; kept = [r for r in db.rows if r[1] not in races]
    nid = max(r[0] for r in db.rows) + 1; new = []
    for race, blk in blocks():
        for var, geoset, scalp in blk['rows']['hairgeo']:
            rid = ids.pop(0) if ids else None
            if rid is None: rid = nid; nid += 1
            new.append([rid, race, blk['sex'], var, geoset, scalp])
    write(os.path.join(dst, 'CharHairGeosets.dbc'), db.nf, sorted(kept + new, key=lambda r: r[0]), set())
    for race, blk in blocks():
        b = sum(1 for r in db.rows if r[1] == race and r[2] == blk['sex'])
        if b and b != len(blk['rows']['hairgeo']):
            notes.append('race %d sex %d: hair styles %d -> %d' % (race, blk['sex'], b, len(blk['rows']['hairgeo'])))
    print('CharHairGeosets: %d rows' % len(new))

    # ---------------- CharacterFacialHairStyles: Race, Sex, Variation, Geoset[5]  (no ID column)
    db = DBC(find('CharacterFacialHairStyles.dbc'))
    kept = [r for r in db.rows if r[0] not in races]; new = []
    for race, blk in blocks():
        for var, g1, g3, g2, g16, g17 in blk['rows']['facialstyles']:
            new.append([race, blk['sex'], var, g1, g3, g2, g16, g17])
    write(os.path.join(dst, 'CharacterFacialHairStyles.dbc'), db.nf, kept + new, set())
    for race, blk in blocks():
        b = sum(1 for r in db.rows if r[0] == race and r[1] == blk['sex'])
        if b and b != len(blk['rows']['facialstyles']):
            notes.append('race %d sex %d: facial styles %d -> %d' % (race, blk['sex'], b, len(blk['rows']['facialstyles'])))
    print('CharacterFacialHairStyles: %d rows' % len(new))

    # ---------------- BarberShopStyle: ID, Type, Name[16]+mask, Add[16]+mask, Cost, Race, Sex, Data
    db = DBC(find('BarberShopStyle.dbc')); S = set(range(2, 18)) | set(range(19, 35))
    old = strings_of(db, S)
    ids = [r[0] for r in old if r[37] in races]; kept = [r for r in old if r[37] not in races]
    nid = max(r[0] for r in old) + 1; new = []
    for race, blk in blocks():
        tmpl = next((r for r in old if r[37] == race), old[0])
        for typ, data, name, cost in blk['rows']['barber']:
            rid = ids.pop(0) if ids else None
            if rid is None: rid = nid; nid += 1
            r = list(tmpl); r[0] = rid; r[1] = typ
            for i in range(2, 18): r[i] = ''
            for i in range(19, 35): r[i] = ''
            r[2] = name[:120]; r[36] = float(cost); r[37] = race; r[38] = blk['sex']; r[39] = data
            new.append(r)
    write(os.path.join(dst, 'BarberShopStyle.dbc'), db.nf, sorted(kept + new, key=lambda r: r[0]), S)
    print('BarberShopStyle: %d rows' % len(new))
    for n in notes: print('  ' + n)
    return notes

def main():
    """standalone: python3 dbcmerge.py <dbc_in> <dbc_out> rows.json   (rows.json as written by hdport)"""
    src, dst, rows = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
    merge(src, dst, rows)

if __name__ == '__main__':
    main()
