#!/usr/bin/env python3
"""
inspect_race - print what a race's race config has to be written from.

  python3 inspect_race.py --tables <retail CSV dir> --chr-model 17 18 \
                          --models <export>/character/goblin/male/goblinmale_hd.m2 \
                                   <export>/character/goblin/female/goblinfemale_hd.m2
                          (no config needed: the step before writing races/<name>.json)
  python3 inspect_race.py --tables <retail CSV dir> [--export <wow.export root>] [--listfile ...] \
                          --race human            (reads chr_model / folders from races/human.json)
  python3 inspect_race.py --tables <dir> --chr-model 1 2          (just the tables)

Per ChrModel:
  * every customization option in order, its choice count, DK-only choices,
  * which texture targets each option's elements write, and what they are related to
    (plain, or per choice of another option, e.g. 'per Hair Style'),
  * which geoset types / values each option sets,
  * image size of the first texture found for each target, and how many are missing from the export.
With --extract-list FILE: a TACTTool list ('fileDataID;path') of every texture the race's customization
uses (TACTTool -m list -i FILE -d "<WoW install>" -o <export root>). Without --listfile the names are
<export_folder>/customization/<fileDataID>_<fileDataID>.blp, which hdport finds by ID.
Per model, given with --models (or found through --race and --export):
  * m2 version, bones, .skel / particles / ribbons / lights (blockers),
  * texture types used, and every geoset ID in skin 00 grouped by group.
"""
import argparse, collections, json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'lib'))
from tables import Tables, ExportFiles

def blp_size(p):
    try:
        with open(p, 'rb') as f: d = f.read(20)
        if d[:4] == b'BLP2': return '%dx%d' % struct.unpack_from('<II', d, 12)
    except OSError: pass
    return '?'

def options_report(t, files, model):
    opts = sorted((o for o in t.opt.values() if int(o['ChrModelID']) == model),
                  key=lambda o: (int(o.get('OrderIndex') or 0), int(o['ID'])))
    cho_opt = {cid: int(c['ChrCustomizationOptionID']) for cid, c in t.cho.items()}
    print('\n== ChrModel %d: %d options' % (model, len(opts)))
    for o in opts:
        ch = t.choices(int(o['ID']))
        dk = sum(1 for c in ch if t.dk_only(c))
        print('\n  option %s %r: %d choices%s' % (o['ID'], o['Name_lang'], len(ch), ', %d DK-only' % dk if dk else ''))
        names = [c['Name_lang'] or '#%s' % c['ID'] for c in ch]
        print('    choices:', ', '.join(names[:40]) + (' ...' if len(names) > 40 else ''))
        rtypes = collections.Counter(t.req_type(c) for c in ch)
        if len(rtypes) > 1 or 3 not in rtypes:
            print('    requirement types:', ', '.join('%d x%d' % kv for kv in sorted(rtypes.items())),
                  '(3 = player; 2 / 4 seen on NPC-only / special choices)')
        lists = collections.defaultdict(list)       # choices limited to choices of another option
        for c in ch:
            a = t.allowed(c)
            if a:
                other = collections.Counter(t.opt.get(cho_opt.get(x, -1), {}).get('Name_lang', '?') for x in a)
                lists[tuple(sorted(other.items()))].append(c)
        for other, cs in lists.items():
            print('    %d choices limited to %s' % (len(cs), ', '.join('%d %s' % (n, o) for o, n in other)))
        tex = collections.defaultdict(lambda: [0, 0, None])     # (target, related option) -> [n, missing, sample]
        geo = collections.defaultdict(set)                      # (type, related option) -> values
        for c in ch:
            for e in t.els[int(c['ID'])]:
                rel = int(e['RelatedChrCustomizationChoiceID'])
                ro = t.opt.get(cho_opt.get(rel, -1), {}).get('Name_lang', '?') if rel else ''
                m = int(e['ChrCustomizationMaterialID'])
                if m and m in t.mat:
                    k = (int(t.mat[m]['ChrModelTextureTargetID']), ro)
                    fd = t.tfd.get(int(t.mat[m]['MaterialResourcesID']), [])
                    p = next((files.find(x) for x in fd if files and files.find(x)), None)
                    tex[k][0] += 1
                    if p is None: tex[k][1] += 1
                    elif tex[k][2] is None: tex[k][2] = p
                g = int(e['ChrCustomizationGeosetID'])
                if g and g in t.geo:
                    geo[(int(t.geo[g]['GeosetType']), ro)].add(int(t.geo[g]['GeosetID']))
        for (tg, ro), (n, miss, p) in sorted(tex.items()):
            print('    texture target %-3d %-22s %4d elements%s  %s' % (
                tg, ('per ' + ro) if ro else 'plain', n, (', %d not in export' % miss) if (files and miss) else '',
                ('%s %s' % (blp_size(p), os.path.basename(p))) if p else ''))
        for (gt, ro), vals in sorted(geo.items()):
            print('    geoset type %-3d %-26s values %s' % (gt, ('per ' + ro) if ro else 'plain', sorted(vals)))

def model_report(path, sexname):
    from m2conv import read_chunks, arr
    from m2info import info
    r, md, ch = info(path)
    folder = os.path.dirname(path); stem = os.path.basename(path)[:-3]
    print('\n== %s model %s' % (sexname, os.path.basename(path)))
    print('  version %d, bones %d, vertices %d, sequences %d, external anims %d' % (
        r['ver'], r['bones'], r['verts'], r['seq'], len(ch.get('AFID', b'')) // 8))
    blockers = []
    if 'SKID' in ch and not r['bones']:
        sk = os.path.join(folder, stem + '.skel')
        if not os.path.exists(sk): blockers.append('bones are in %s.skel, which is not in the export' % stem)
        else:
            from m2conv import read_chunks as rc
            skc = rc(sk)
            print('  skeleton %s.skel: %d bones, %d sequences' % (stem, arr(skc['SKB1'], 0)[0], arr(skc['SKS1'], 8)[0]))
            if 'SKPD' in skc: blockers.append('skeleton inherits from a parent skeleton (SKPD): not supported yet')
    for k in ('particles', 'ribbons', 'lights'):
        if r[k]: blockers.append('%d %s (converter stops on these)' % (r[k], k))
    print('  blockers:', '; '.join(blockers) if blockers else 'none')
    types = collections.Counter(t for t, _ in r['textures'])
    print('  texture types:', ', '.join('%d x%d' % kv for kv in sorted(types.items())))
    skin = os.path.join(folder, stem + '00.skin')
    if not os.path.exists(skin):
        print('  no %s00.skin next to it' % stem); return
    s = open(skin, 'rb').read()
    nsm, osm = arr(s, 28); nb, ob = arr(s, 36)
    ids = [struct.unpack_from('<H', s, osm + 48*i)[0] for i in range(nsm)]
    tex_of = collections.defaultdict(set)
    nt, ot = arr(md, 80); tt = [struct.unpack_from('<I', md, ot + 16*i)[0] for i in range(nt)]
    nc, oc = arr(md, 128); combos = struct.unpack_from('<%dH' % nc, md, oc)
    for i in range(nb):
        b = struct.unpack_from('<BBHHHHHHHHHHH', s, ob + 24*i)
        tex_of[ids[b[3]]].add(tt[combos[b[9]]])
    groups = collections.defaultdict(list)
    for g in sorted(set(ids)): groups[g // 100].append(g)
    print('  geosets in skin 00 (texture types drawn):')
    for g, v in sorted(groups.items()):
        print('    group %-3d %s' % (g, ' '.join('%d%s' % (x, '' if tex_of[x] == {1} else str(sorted(tex_of[x])))
                                              for x in v)))

def model_label(path):
    """'male' / 'female' from a retail stem like goblinfemale_hd, else the stem itself"""
    stem = os.path.basename(path).lower()
    return 'female' if 'female' in stem else 'male' if 'male' in stem else stem

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tables'); ap.add_argument('--export'); ap.add_argument('--listfile')
    ap.add_argument('--race'); ap.add_argument('--chr-model', type=int, nargs='*')
    ap.add_argument('--models', nargs='+', metavar='M2',
                    help='retail .m2 files to inspect directly (the .skel and 00.skin next to them are read too); '
                         'no race config needed')
    ap.add_argument('--extract-list', help='write a TACTTool extraction list of the customization textures')
    a = ap.parse_args()
    if not (a.tables or a.models):
        ap.error('nothing to inspect: give --tables (with --chr-model or --race) and/or --models')
    cfg = json.load(open(os.path.join(HERE, 'races', a.race + '.json'))) if a.race else None
    files = ExportFiles(a.export, a.listfile) if a.export else None
    models = a.chr_model or ([sx['chr_model'] for sx in cfg['sexes'].values()] if cfg else None)
    if (a.tables or a.extract_list) and not models:
        ap.error('which ChrModels? give --chr-model (e.g. 17 18) or --race')
    if a.tables:
        t = Tables(a.tables)
        for m in models: options_report(t, files, m)
    if a.extract_list:
        if not a.tables: raise SystemExit('--extract-list needs --tables')
        names = {}
        if a.listfile:
            with open(a.listfile, encoding='utf-8', errors='replace') as f:
                for line in f:
                    x, _, y = line.strip().partition(';')
                    if x.isdigit(): names[int(x)] = y
        folder = cfg['export_folder'] if cfg else 'character/customization'
        fds = set()
        for o in t.opt.values():
            if int(o['ChrModelID']) in models:
                for c in t.choices(int(o['ID'])):
                    for e in t.els[int(c['ID'])]:
                        m = int(e['ChrCustomizationMaterialID'])
                        if m in t.mat: fds.update(t.tfd.get(int(t.mat[m]['MaterialResourcesID']), []))
        have = sum(1 for x in fds if files and files.find(x))
        with open(a.extract_list, 'w') as f:
            for x in sorted(fds):
                f.write('%d;%s\n' % (x, names.get(x) or '%s/customization/%d_%d.blp' % (folder, x, x)))
        print('\nwrote %s: %d textures (%d already in the export)' % (a.extract_list, len(fds), have))
    if a.models:
        for p in a.models:
            if not os.path.isfile(p): raise SystemExit('model not found: %s' % p)
            model_report(p, model_label(p))
    elif cfg and files:
        for sexname, sx in cfg['sexes'].items():
            p = files.path('%s/%s/%s.m2' % (cfg['export_folder'], sexname, sx['model']))
            if p: model_report(p, sexname)
            else: print('\n== %s: %s/%s/%s.m2 not in export' % (sexname, cfg['export_folder'], sexname, sx['model']))

if __name__ == '__main__':
    main()
