#!/usr/bin/env python3
"""
hdport - convert retail (modern) HD character models to 3.3.5a and update the DBCs.

  python3 hdport.py --export <wow.export root> --tables <dir with table CSVs> \
                    --dbc <client DBFilesClient dir> --out <output dir> \
                    [--listfile community-listfile.csv] [--race worgen human ...]

For each race config in races/*.json (or the ones named with --race):
  * converts <export>/<export_folder>/<sex>/<model>.m2 (+ .skin, .anim) to 3.3.5a m2 + 4 skins
  * rebuilds every customization texture in the 3.3.5a layout and format
  * collects CharSections / CharHairGeosets / CharacterFacialHairStyles / BarberShopStyle rows
then writes the merged DBCs (all other races untouched) and report.txt.

Output: <out>/patch/  -> copy into your patch MPQ folder; <out>/patch/DBFilesClient also goes to
the server's data/dbc.  <out>/rows.json lets dbcmerge.py re-apply the rows to other DBC copies.
"""
import argparse, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'lib'))
import m2conv
from tables import Tables, ExportFiles
from assets import Builder
from dbcmerge import DBC, merge

def find_ci(folder, name):
    for f in os.listdir(folder):
        if f.lower() == name.lower(): return os.path.join(folder, f)
    return None

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--export', required=True); ap.add_argument('--tables', required=True)
    ap.add_argument('--dbc', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--listfile'); ap.add_argument('--race', nargs='*')
    ap.add_argument('--max-bones', type=int, default=64)
    ap.add_argument('--layout', choices=['stock', 'retail1024'], default='stock',
                    help="character texture layout: 'stock' 512 (any client), 'retail1024' (client patched with "
                         "patch_component_layout.py; every race must be built with the same layout)")
    a = ap.parse_args()
    if a.race: a.race = ['forsaken' if r == 'scourge' else r for r in a.race]   # folder name -> config name

    cfgs = []
    for f in sorted(os.listdir(os.path.join(HERE, 'races'))):
        if f.endswith('.json'):
            c = json.load(open(os.path.join(HERE, 'races', f)))
            if (not a.race and not c.get('draft')) or (a.race and c['name'] in a.race): cfgs.append(c)
            elif not a.race: print('skipping draft config %s (name it with --race to build it)' % c['name'])
    if a.race:
        known = {json.load(open(os.path.join(HERE, 'races', f)))['name'] for f in os.listdir(os.path.join(HERE, 'races')) if f.endswith('.json')}
        unknown = sorted(set(a.race) - known)
        if unknown: raise SystemExit('no race config named %s (configs: %s)' % (', '.join(unknown), ', '.join(sorted(known))))
    if not cfgs: raise SystemExit('no race configs selected')
    print('races:', ', '.join(c['name'] for c in cfgs))

    t0 = time.time()
    tables = Tables(a.tables)
    files = ExportFiles(a.export, a.listfile)
    print('export: %d files indexed, listfile names %d' % (len(files.index), len(files.names)))
    anim_ids = {r[0] for r in DBC(find_ci(a.dbc, 'AnimationData.dbc')).rows}
    patch = os.path.join(a.out, 'patch'); report = []
    allrows = {}
    for cfg in cfgs:
        print('== %s (race %d)' % (cfg['name'], cfg['race_id']))
        cfg['layout'] = a.layout
        m2conv.CFG = cfg
        b = Builder(cfg, tables, files, patch, report)
        allrows[str(cfg['race_id'])] = {}
        for sexname, sx in cfg['sexes'].items():
            src_dir = files.path('%s/%s' % (cfg['export_folder'], sexname))
            src = files.path('%s/%s/%s.m2' % (cfg['export_folder'], sexname, sx['model']))
            if not src:
                report.append('%s %s: %s.m2 not found in export, skipped' % (cfg['name'], sexname, sx['model'])); continue
            skin = files.path('%s/%s/%s00.skin' % (cfg['export_folder'], sexname, sx['model']))
            disk = cfg.get('disk_folders', {}).get('%s\\%s' % (cfg['out_folder'], sx['folder_cap']),
                                                   '%s/%s' % (cfg['out_folder'].replace('\\', '/'), sx['folder_cap']))
            out_dir = os.path.join(patch, *disk.split('/'))
            lay = m2conv.convert(src, out_dir, sx.get('out_model', sx['model']), anim_ids, [skin]*4, a.max_bones, sexname, sx['model_cap'])
            # where the face area and any moved body pieces went: rebake.py converts retail NPC bakes with it
            os.makedirs(os.path.join(a.out, 'layouts'), exist_ok=True)
            json.dump(lay, open(os.path.join(a.out, 'layouts', '%d_%s.json' % (cfg['race_id'], sexname)), 'w'), indent=1)
            # stems on disk keep the old (lowercase) names so they overwrite the worgoblin files cleanly
            rows, info = b.build(sexname, lay)
            allrows[str(cfg['race_id'])][sexname] = {'sex': sx['sex'], 'rows': rows, 'info': info}
    json.dump(allrows, open(os.path.join(a.out, 'rows.json'), 'w'), indent=0)
    for n in merge(a.dbc, os.path.join(patch, 'DBFilesClient'), allrows):
        report.append('DBC: ' + n + ' (existing characters/NPCs keep their old index)')
    with open(os.path.join(a.out, 'report.txt'), 'w') as f:
        f.write('hdport report\n\n')
        for race, sexes in allrows.items():
            for s, blk in sexes.items():
                f.write('race %s %s: %s\n' % (race, s, json.dumps(blk['info'])))
        f.write('\nnotes:\n' + ''.join('  - %s\n' % r for r in dict.fromkeys(report)))
    print('done in %.0fs; %d notes in report.txt' % (time.time() - t0, len(report)))

if __name__ == '__main__':
    main()
