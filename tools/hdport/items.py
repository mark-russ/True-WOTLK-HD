#!/usr/bin/env python3
"""
items - convert retail item models (helmets, shoulders, weapons, shields, ...) to 3.3.5a,
keeping only real upgrades.

  python3 items.py --export <retail export root> --dbc <client DBFilesClient> \
                   --listfile community-listfile.csv --out build_items \
                   [--old <3.3.5a files root>] [--patch <your patch MPQ folder>] [--all]

A retail model is converted when it is an actual upgrade:
  * with --old: its geometry differs from the 3.3.5a file of the same name (vertex/bone count),
    or no 3.3.5a file exists
  * without --old: it was re-authored after Cataclysm (m2 version 274+). v272 files are Cata
    re-saves of the original geometry, so they bring nothing.
--all converts everything regardless.

--patch: an existing patch folder (e.g. patch-A.MPQ). Output files reuse the exact names and folder
case found there, so copying the output over it replaces files instead of creating case twins.

Textures: the item's own texture still comes from ItemDisplayInfo, so a converted model keeps using
the 3.3.5a texture unless the texture pass (later) upgrades it. Fixed textures the model names
itself are copied next to it.
"""
import argparse, collections, os, shutil, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'lib'))
import m2conv
import numpy as np
from m2info import info
from dbcmerge import DBC

def walk(root):
    out = {}
    if not root: return out
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace(os.sep, '/').lower()] = p
    return out

class CaseMap:
    """maps lowercase output paths onto the case already used in an existing patch folder"""
    def __init__(self, root):
        self.files = {}; self.dirs = {}
        if not root: return
        for d, ds, fs in os.walk(root):
            rel = os.path.relpath(d, root).replace(os.sep, '/')
            if rel != '.': self.dirs[rel.lower()] = rel
            for f in fs:
                r = (rel + '/' + f) if rel != '.' else f
                self.files[r.lower()] = r
    def get(self, rel, wotlk_name=None):
        rel = rel.lower()
        if rel in self.files: return self.files[rel]
        d, f = rel.rsplit('/', 1)
        parts = d.split('/'); out = []
        for i in range(len(parts)):
            k = '/'.join(parts[:i+1]); out.append(self.dirs[k].split('/')[-1] if k in self.dirs else parts[i])
        return '/'.join(out) + '/' + (wotlk_name or f)

def positions(md):
    n, o = struct.unpack_from('<II', md, 60)
    return np.frombuffer(md, '<f4', n*12, o).reshape(n, 12)[:, :3] if n else np.zeros((0, 3), np.float32)

def same_geometry(md_old, md_new):
    """True when the retail file is the 3.3.5a mesh re-saved: same vertices in the same places"""
    a, b = positions(md_old), positions(md_new)
    if a.shape != b.shape: return False
    if not len(a) or float(np.abs(a - b).max()) < 1e-3: return True
    # re-saves sometimes reorder vertices: compare the point sets
    sa = np.unique(np.round(a, 3), axis=0); sb = np.unique(np.round(b, 3), axis=0)
    return sa.shape == sb.shape and bool(np.allclose(sa, sb, atol=2e-3))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--export', required=True); ap.add_argument('--dbc', required=True)
    ap.add_argument('--listfile'); ap.add_argument('--out', required=True)
    ap.add_argument('--old'); ap.add_argument('--patch'); ap.add_argument('--all', action='store_true')
    ap.add_argument('--folders', nargs='*', default=['item/objectcomponents'])
    a = ap.parse_args()

    for flag, path in (('--export', a.export), ('--patch', a.patch), ('--old', a.old), ('--dbc', a.dbc)):
        if path and not os.path.isdir(path):
            raise SystemExit('%s: %r is not a folder' % (flag, path))
    outp = os.path.realpath(a.out)
    for flag, path in (('--export', a.export), ('--patch', a.patch), ('--dbc', a.dbc)):
        if path and (outp == os.path.realpath(path) or outp.startswith(os.path.realpath(path) + os.sep)):
            raise SystemExit('--out must not be inside %s (%s): write to a separate folder, then copy' % (flag, path))
    retail = walk(a.export); old = walk(a.old); case = CaseMap(a.patch)
    if a.patch:
        n_item = sum(1 for k in case.files if k.startswith('item/'))
        print('patch folder: %d files, %d under item/' % (len(case.files), n_item))
        if not n_item:
            raise SystemExit('--patch %r has no item/ folder; point it at the patch folder itself '
                             '(the one containing ITEM/ and DBFilesClient/)' % a.patch)
    names = {}
    if a.listfile:
        with open(a.listfile, encoding='utf-8', errors='replace') as f:
            for line in f:
                x, _, y = line.rstrip('\n').partition(';')
                if x.isdigit(): names[int(x)] = y
    anim_ids = {r[0] for r in DBC(next(os.path.join(a.dbc, f) for f in os.listdir(a.dbc) if f.lower() == 'animationdata.dbc')).rows}
    # what the client currently has: your patch folder wins over the base MPQ exports
    old_by_name = collections.defaultdict(list)
    for k, p in walk(a.patch).items(): old_by_name[k.rsplit('/', 1)[-1]].append(p)
    for k, p in old.items(): old_by_name[k.rsplit('/', 1)[-1]].append(p)

    stats = collections.Counter(); notes = []
    models = sorted(k for k in retail if k.endswith('.m2') and any(k.startswith(f) for f in a.folders)) or \
             sorted(k for k in retail if k.endswith('.m2'))
    for rel in models:
        src = retail[rel]; base = rel.rsplit('/', 1)[-1]
        try:
            r, md, ch = info(src)
        except Exception as e:
            stats['retail file unreadable (skipped)'] += 1; notes.append('%s: %s' % (rel, e)); continue
        o = old_by_name.get(base)
        if not a.all:
            try:
                ro, md_old, _ = info(o[0]) if o else (None, None, None)
            except Exception as e:
                notes.append('%s: could not read your current file %s (%s); converting anyway' % (rel, o[0], e))
                stats['current file unreadable (converted anyway)'] += 1
                o = None; ro = md_old = None
            if o:
                if same_geometry(md_old, md):
                    stats['unchanged since 3.3.5a (skipped)'] += 1; continue
                reason = 'geometry changed (%d->%d verts)' % (ro['verts'], r['verts'])
            elif old or a.patch:
                reason = 'no 3.3.5a file'
                if r['ver'] < 274:
                    stats['no 3.3.5a counterpart, pre-Legion (skipped)'] += 1; continue
            else:
                if r['ver'] < 274:
                    stats['pre-Legion re-save (skipped)'] += 1; continue
                reason = 'remade after Cataclysm (v%d)' % r['ver']
        else:
            reason = 'forced'
        if r['particles'] or r['ribbons'] or r['lights']:
            stats['has particles/ribbons/lights (waiting for that converter)'] += 1
            notes.append('%s: %d particles, %d ribbons, %d lights' % (rel, r['particles'], r['ribbons'], r['lights'])); continue
        skin = retail.get(rel[:-3] + '00.skin')
        if not skin:
            stats['missing .skin in export'] += 1; notes.append(rel + ': no 00.skin'); continue
        out_rel = case.get(rel)
        out_dir = os.path.join(a.out, *out_rel.split('/')[:-1])
        stem = out_rel.split('/')[-1][:-3]
        folder_bs = '\\'.join(out_rel.split('/')[:-1])
        copies = []
        def resolver(fdid, _copies=copies, _dir=folder_bs):
            n = names.get(fdid)
            fn = (n.rsplit('/', 1)[-1] if n else '%s_%d.blp' % (stem, fdid))
            _copies.append((fdid, n, fn))
            return _dir + '\\' + fn
        m2conv.CFG = {'body_layout': False, 'geosets': {'keep_all': True}, 'hardcoded_resolver': resolver}
        try:
            m2conv.convert(src, out_dir, stem, anim_ids, [skin], 64, 'item', stem)
        except Exception as e:
            stats['conversion error'] += 1; notes.append('%s: %s' % (rel, e)); continue
        # give the files the exact names already used in the patch folder (no case twins)
        want_m2 = os.path.join(a.out, *out_rel.split('/'))
        got_m2 = os.path.join(out_dir, stem + '.m2')
        if got_m2 != want_m2: os.replace(got_m2, want_m2)
        skin_rel = case.get(rel[:-3] + '00.skin')
        want_skin = os.path.join(a.out, *skin_rel.split('/'))
        got_skin = os.path.join(out_dir, stem + '00.skin')
        if got_skin != want_skin: os.replace(got_skin, want_skin)
        for fdid, n, fn in copies:
            srcp = retail.get(n.lower()) if n else None
            if not srcp:
                srcp = next((p for k, p in retail.items() if k.endswith('_%d.blp' % fdid)), None)
            if srcp: shutil.copyfile(srcp, os.path.join(out_dir, fn))
            else: notes.append('%s: fixed texture %d not in export' % (rel, fdid))
        stats['converted'] += 1; notes.append('%s: converted (%s)' % (rel, reason))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'items_report.txt'), 'w') as f:
        for k, v in sorted(stats.items()): f.write('%-60s %d\n' % (k, v))
        f.write('\n' + '\n'.join(notes) + '\n')
    print(open(os.path.join(a.out, 'items_report.txt')).read())

if __name__ == '__main__':
    main()
