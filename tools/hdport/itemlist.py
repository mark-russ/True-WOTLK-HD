#!/usr/bin/env python3
"""
itemlist - work out which retail files the 3.3.5a client's item displays need, and write an
extraction list for TACTTool (one 'fileDataID;path' per line).

  pass 1 (from tables):
    python3 itemlist.py --dbc <client DBFilesClient> --tables <retail CSV dir> \
                        --listfile community-listfile.csv --out items/
        -> items/extract.txt        models + textures named by the retail tables
        -> items/displays.json      per 3.3.5a display: which retail files replace which old names
        -> items/report.txt

    TACTTool -m list -i items/extract.txt -d "<WoW install>" -o <export root>

  pass 2 (after extracting pass 1): files only the models themselves know about
    python3 itemlist.py --complete <export root> --listfile community-listfile.csv --out items/
        -> items/extract_pass2.txt  .skin / .anim / .skel / fixed textures referenced inside the m2s

Matching: Blizzard kept the old ItemDisplayInfo IDs, so each 3.3.5a display is looked up by ID, then
every retail file is checked against the old file name (race/sex suffixes ignored). A display whose
retail files carry different names is treated as reused and skipped, so it can't pull the wrong item.
"""
import argparse, collections, json, os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'lib'))
from dbcmerge import DBC
from tables import read_csv

# 3.3.5a race -> retail RaceID (worgoblin: worgen on 12, goblin on 9)
RACES = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10, 11: 11, 12: 22}
SECTIONS = ['ArmUpper', 'ArmLower', 'Hand', 'TorsoUpper', 'TorsoLower', 'LegUpper', 'LegLower', 'Foot']
SUFFIX = re.compile(r'_(hu|or|dw|ni|sc|ta|gn|tr|go|be|dr|wo|ma|vu|ko|pa|nb|ng|lf|hm|dr)[mf]$|_[umf]$')

def stem(name):
    s = os.path.splitext(os.path.basename(name.replace('\\', '/')))[0].lower()
    return SUFFIX.sub('', s)

def ints(v):
    return [int(x) for x in str(v).split(',')] if v not in ('', None) else []

def load_listfile(path, keep=None):
    names = {}
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            a, _, b = line.rstrip('\n').partition(';')
            if a.isdigit():
                i = int(a)
                if keep is None or i in keep: names[i] = b.lower()
    return names

OUR_SUFFIX = {'hu', 'or', 'dw', 'ni', 'sc', 'ta', 'gn', 'tr', 'go', 'be', 'dr', 'wo'}
RACE_SFX = re.compile(r'_([a-z]{2})[mf]$')

def name_index(listfile):
    """stem -> {fdid: path} for item art, keeping only race variants for the races we have."""
    idx = collections.defaultdict(dict)
    with open(listfile, encoding='utf-8', errors='replace') as f:
        for line in f:
            a_, _, b = line.rstrip('\n').partition(';')
            b = b.lower()
            if not a_.isdigit() or not b.startswith('item/') or not b.endswith(('.m2', '.blp')): continue
            base = os.path.splitext(os.path.basename(b))[0]
            m = RACE_SFX.search(base)
            if m and m.group(1) not in OUR_SUFFIX and SUFFIX.search(base): continue
            idx[(stem(b), os.path.splitext(b)[1])][int(a_)] = b
    return idx

def pass1(a):
    t = lambda n: read_csv(os.path.join(a.tables, n + '.csv'))
    print('loading tables...')
    idi = {int(r['ID']): r for r in t('ItemDisplayInfo')}
    mfd = collections.defaultdict(list)
    for r in t('ModelFileData'): mfd[int(r['ModelResourcesID'])].append(int(r['FileDataID']))
    tfd = collections.defaultdict(list)
    for r in t('TextureFileData'): tfd[int(r['MaterialResourcesID'])].append(int(r['FileDataID']))
    cmfd = {int(r['ID']): r for r in t('ComponentModelFileData')}
    ctfd = {int(r['ID']): r for r in t('ComponentTextureFileData')}
    matres = collections.defaultdict(list)
    for r in t('ItemDisplayInfoMaterialRes'): matres[int(r['ItemDisplayInfoID'])].append((int(r['ComponentSection']), int(r['MaterialResourcesID'])))
    modelmat = collections.defaultdict(list)
    for r in t('ItemDisplayInfoModelMatRes'): modelmat[int(r['ItemDisplayInfoID'])].append((int(r['ModelIndex']), int(r['TextureType']), int(r['MaterialResourcesID'])))
    wanted_races = set(RACES.values()) | {0}

    def race_ok(fdid, table):
        r = table.get(fdid)
        return r is None or int(r['RaceID']) in wanted_races

    # collect every candidate FDID first, then load just those names from the listfile
    db = DBC(os.path.join(a.dbc, next(f for f in os.listdir(a.dbc) if f.lower() == 'itemdisplayinfo.dbc')))
    plan = []
    cand = set()
    for row in db.rows:
        did = row[0]
        old_models = [db.str(row[1]), db.str(row[2])]
        old_mtex = [db.str(row[3]), db.str(row[4])]
        old_reg = [db.str(row[15 + k]) for k in range(8)]
        if not any(old_models) and not any(old_mtex) and not any(old_reg): continue
        r = idi.get(did)
        entry = dict(id=did, old_models=old_models, old_mtex=old_mtex, old_regions=old_reg, retail=r is not None,
                     models=[[], []], mtex=[[], []], regions=[[] for _ in range(8)])
        if r:
            mres = ints(r['ModelResourcesID']); mmres = ints(r['ModelMaterialResourcesID'])
            for i in range(2):
                if i < len(mres) and mres[i]:
                    entry['models'][i] = [f for f in mfd.get(mres[i], []) if race_ok(f, cmfd)]
                if i < len(mmres) and mmres[i]:
                    entry['mtex'][i] = list(tfd.get(mmres[i], []))
            for mi, ttype, mr in modelmat.get(did, []):
                if 0 <= mi < 2: entry['mtex'][mi] += [f for f in tfd.get(mr, []) if f not in entry['mtex'][mi]]
            for sec, mr in matres.get(did, []):
                if 0 <= sec < 8:
                    entry['regions'][sec] += [f for f in tfd.get(mr, []) if race_ok(f, ctfd)]
            for lst in entry['models'] + entry['mtex'] + entry['regions']: cand.update(lst)
        plan.append(entry)
    print('client displays with art: %d   candidate retail files: %d' % (len(plan), len(cand)))
    names = load_listfile(a.listfile, cand)

    # match by name
    out = {}; extract = {}
    stats = collections.Counter(); reused = []
    def take(fdids, old, kind, ext):
        """keep retail files whose name matches the old one; unnamed files are kept only when
        the display has no named files at all (new HD variants are often unnamed)"""
        if not old or not fdids: return []
        o = stem(old)
        named = [f for f in fdids if f in names]
        good = [f for f in named if stem(names[f]) == o]
        if named and not good:
            return None                                  # names disagree -> display was reused
        if not named:
            good = list(fdids); stats[kind + ' unnamed (kept)'] += len(good)
        for f in good:
            extract[f] = names.get(f) or 'unknown/%s/%d.%s' % (kind, f, ext)
        return good
    print('indexing item names in listfile...')
    nidx = name_index(a.listfile)
    def by_name(old, ext, kind):
        if not old: return []
        hits = nidx.get((stem(old), ext), {})
        if kind == 'model' and ext == '.m2':
            pass
        for f, p in hits.items(): extract[f] = p
        if hits: stats['%s resolved by name' % kind] += len(hits)
        return list(hits)
    for e in plan:
        res = dict(models=[], mtex=[], regions={})
        if not e['retail']:
            stats['displays dropped by retail (resolved by name)'] += 1
            for i in range(2):
                res['models'].append(by_name(e['old_models'][i], '.m2', 'model'))
                res['mtex'].append(by_name(e['old_mtex'][i], '.blp', 'texture'))
            for k in range(8):
                g = by_name(e['old_regions'][k], '.blp', 'texture')
                if g: res['regions'][SECTIONS[k]] = g
            if any(res['models']) or any(res['mtex']) or res['regions']:
                out[e['id']] = dict(res, old_models=e['old_models'], old_mtex=e['old_mtex'], old_regions=e['old_regions'], by_name=True)
            else:
                stats['displays with no retail files'] += 1
            continue
        bad = False
        for i in range(2):
            m = take(e['models'][i], e['old_models'][i], 'model', 'm2')
            tx = take(e['mtex'][i], e['old_mtex'][i], 'texture', 'blp')
            if m == [] and e['old_models'][i]: m = by_name(e['old_models'][i], '.m2', 'model')
            if tx == [] and e['old_mtex'][i]: tx = by_name(e['old_mtex'][i], '.blp', 'texture')
            if m is None or tx is None: bad = True
            res['models'].append(m or []); res['mtex'].append(tx or [])
        for k in range(8):
            g = take(e['regions'][k], e['old_regions'][k], 'texture', 'blp')
            if g == [] and e['old_regions'][k]: g = by_name(e['old_regions'][k], '.blp', 'texture')
            if g is None: bad = True
            elif g: res['regions'][SECTIONS[k]] = g
        if bad:
            stats['displays reused for other art (skipped)'] += 1; reused.append(e['id']); continue
        if any(res['models']) or any(res['mtex']) or res['regions']:
            stats['displays matched'] += 1
            out[e['id']] = dict(res, old_models=e['old_models'], old_mtex=e['old_mtex'], old_regions=e['old_regions'])
        else:
            stats['displays with no retail files'] += 1
    # extraction list keeps retail paths so the export has the usual folder structure
    os.makedirs(a.out, exist_ok=True)
    kinds = collections.Counter()
    with open(os.path.join(a.out, 'extract.txt'), 'w') as f:
        for fd, p in sorted(extract.items()):
            f.write('%d;%s\n' % (fd, p)); kinds[os.path.splitext(p)[1]] += 1
    json.dump(out, open(os.path.join(a.out, 'displays.json'), 'w'))
    folders = collections.Counter('/'.join(p.split('/')[:3]) for p in extract.values())
    with open(os.path.join(a.out, 'report.txt'), 'w') as f:
        f.write('itemlist pass 1\n\n')
        for k, v in sorted(stats.items()): f.write('%-45s %d\n' % (k, v))
        f.write('\nfiles to extract: %d  (%s)\n\nby folder:\n' % (len(extract), ', '.join('%s %d' % kv for kv in kinds.items())))
        for k, v in folders.most_common(): f.write('  %-50s %d\n' % (k, v))
        f.write('\nreused display IDs (first 200): %s\n' % reused[:200])
    print(open(os.path.join(a.out, 'report.txt')).read())

def m2_refs(path):
    """FDIDs a modern m2 references in its own chunks (skins, anims, skeleton, textures...)."""
    d = open(path, 'rb').read(); o = 0; refs = collections.defaultdict(list)
    if d[:4] != b'MD21': return refs
    while o + 8 <= len(d):
        tag = d[o:o+4].decode('latin1'); sz = struct.unpack_from('<I', d, o+4)[0]; body = d[o+8:o+8+sz]
        if tag in ('SFID', 'TXID', 'SKID', 'BFID', 'PFID', 'GPID'):
            refs[tag] += [x for x in struct.unpack('<%dI' % (len(body)//4), body) if x]
        elif tag == 'AFID':
            refs[tag] += [struct.unpack_from('<HHI', body, 8*i)[2] for i in range(len(body)//8)]
        o += 8 + sz
    for k in refs: refs[k] = [x for x in refs[k] if x]
    return refs

EXT = {'SFID': '.skin', 'AFID': '.anim', 'TXID': '.blp', 'SKID': '.skel', 'BFID': '.bone', 'PFID': '.phys', 'GPID': '.gpid'}

def pass2(a):
    """list files the extracted m2s reference that aren't in the export yet. Files the listfile
    can't name go next to their model as '<model>_<fileDataID>.<ext>', which is where hdport looks."""
    have = set(); todo = {}; per_model = []
    for d, _, fs in os.walk(a.complete):
        for f in fs:
            p = os.path.join(d, f); have.add(os.path.relpath(p, a.complete).replace(os.sep, '/').lower())
            if f.lower().endswith('.m2'):
                per_model.append((os.path.relpath(p, a.complete).replace(os.sep, '/').lower(), m2_refs(p)))
    names = load_listfile(a.listfile, {x for _, r in per_model for ids in r.values() for x in ids})
    kinds = collections.Counter(); unnamed = 0
    for model, refs in per_model:
        base = model[:-3]
        for tag, ids in refs.items():
            for fd in ids:
                p = names.get(fd)
                if not p:
                    p = '%s_%d%s' % (base, fd, EXT.get(tag, '')); unnamed += 1
                if p not in have and fd not in todo:
                    todo[fd] = p; kinds[EXT.get(tag, tag)] += 1
    with open(os.path.join(a.out, 'extract_pass2.txt'), 'w') as f:
        for fd, p in sorted(todo.items()): f.write('%d;%s\n' % (fd, p))
    print('pass 2: %d further files referenced by %d extracted models' % (len(todo), len(per_model)))
    print('  by type: ' + ', '.join('%s %d' % kv for kv in kinds.most_common()))
    print('  without a listfile name (placed next to their model): %d' % unnamed)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dbc'); ap.add_argument('--tables'); ap.add_argument('--listfile', required=True)
    for flag in ('--listfile', '--complete', '--out', '--dbc', '--tables'):
        if sys.argv.count(flag) > 1: raise SystemExit('%s given more than once' % flag)
    ap.add_argument('--out', required=True); ap.add_argument('--complete')
    a = ap.parse_args()
    pass2(a) if a.complete else pass1(a)
