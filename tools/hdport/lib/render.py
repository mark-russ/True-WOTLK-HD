import numpy as np
from PIL import Image
def render(P, N, tris, colors, W=400, H=600, view='front', uv=None, tex=None, bounds=None):
    # P: (n,3) x forward? WoW: x=forward, y=left, z=up. front view looks along -x: screen x = -y, screen y = z
    if view=='front': sx, sy, depth = -P[:,1], P[:,2], P[:,0]
    elif view=='side': sx, sy, depth = P[:,0], P[:,2], P[:,1]
    elif view=='back': sx, sy, depth = P[:,1], P[:,2], -P[:,0]
    if bounds is None: bounds=(sx.min(),sx.max(),sy.min(),sy.max())
    x0,x1,y0,y1=bounds; sc=min((W-20)/(x1-x0),(H-20)/(y1-y0))
    X=(sx-x0)*sc+10; Y=H-((sy-y0)*sc+10)
    img=np.full((H,W,3),30,np.float32); zb=np.full((H,W),-1e9,np.float32)
    L=np.array([0.6,0.3,0.7]); L/=np.linalg.norm(L)
    for ti,t in enumerate(tris):
        a,b,c=t
        xs=X[[a,b,c]]; ys=Y[[a,b,c]]; zs=depth[[a,b,c]]
        minx=max(int(xs.min()),0); maxx=min(int(xs.max())+1,W-1); miny=max(int(ys.min()),0); maxy=min(int(ys.max())+1,H-1)
        if minx>maxx or miny>maxy: continue
        gx,gy=np.meshgrid(np.arange(minx,maxx+1)+0.5,np.arange(miny,maxy+1)+0.5)
        d=(ys[1]-ys[2])*(xs[0]-xs[2])+(xs[2]-xs[1])*(ys[0]-ys[2])
        if abs(d)<1e-9: continue
        w0=((ys[1]-ys[2])*(gx-xs[2])+(xs[2]-xs[1])*(gy-ys[2]))/d
        w1=((ys[2]-ys[0])*(gx-xs[2])+(xs[0]-xs[2])*(gy-ys[2]))/d
        w2=1-w0-w1
        m=(w0>=0)&(w1>=0)&(w2>=0)
        if not m.any(): continue
        z=w0*zs[0]+w1*zs[1]+w2*zs[2]
        sub=zb[miny:maxy+1,minx:maxx+1]; m&=z>sub
        if not m.any(): continue
        n=N[a]+N[b]+N[c]; n=n/ (np.linalg.norm(n)+1e-9); sh=0.35+0.65*abs(np.dot(n,L))
        if tex is not None and uv is not None:
            tu=w0*uv[a,0]+w1*uv[b,0]+w2*uv[c,0]; tv=w0*uv[a,1]+w1*uv[b,1]+w2*uv[c,1]
            th,tw=tex.shape[:2]
            px=np.clip((tu%1.0)*tw,0,tw-1).astype(int); py=np.clip((tv%1.0)*th,0,th-1).astype(int)
            col=tex[py,px,:3]*sh
            img[miny:maxy+1,minx:maxx+1][m]=col[m]
        else:
            img[miny:maxy+1,minx:maxx+1][m]=np.array(colors[ti])*sh
        sub[m]=z[m]
    return Image.fromarray(np.clip(img,0,255).astype(np.uint8))

def render_multi(P, N, UV, layers, W=400, H=600, view='front', bounds=None, light=1.0, glow_tex=None):
    if view=='front': sx, sy, depth = -P[:,1], P[:,2], P[:,0]
    elif view=='side': sx, sy, depth = P[:,0], P[:,2], P[:,1]
    else: sx, sy, depth = P[:,1], P[:,2], -P[:,0]
    x0,x1,y0,y1=bounds; sc=min((W-20)/(x1-x0),(H-20)/(y1-y0))
    X=(sx-x0)*sc+10; Y=H-((sy-y0)*sc+10)
    img=np.full((H,W,3),40,np.float32); zb=np.full((H,W),-1e9,np.float32)
    L=np.array([0.5,0.3,0.8]); L/=np.linalg.norm(L)
    for tex, tris in layers:
        th,tw=tex.shape[:2]
        for a,b,c in tris:
            xs=X[[a,b,c]]; ys=Y[[a,b,c]]
            minx=max(int(xs.min()),0); maxx=min(int(xs.max())+1,W-1); miny=max(int(ys.min()),0); maxy=min(int(ys.max())+1,H-1)
            if minx>maxx or miny>maxy: continue
            d=(ys[1]-ys[2])*(xs[0]-xs[2])+(xs[2]-xs[1])*(ys[0]-ys[2])
            if abs(d)<1e-9: continue
            gx,gy=np.meshgrid(np.arange(minx,maxx+1)+0.5,np.arange(miny,maxy+1)+0.5)
            w0=((ys[1]-ys[2])*(gx-xs[2])+(xs[2]-xs[1])*(gy-ys[2]))/d
            w1=((ys[2]-ys[0])*(gx-xs[2])+(xs[0]-xs[2])*(gy-ys[2]))/d
            w2=1-w0-w1
            m=(w0>=0)&(w1>=0)&(w2>=0)
            if not m.any(): continue
            z=w0*depth[a]+w1*depth[b]+w2*depth[c]
            sub=zb[miny:maxy+1,minx:maxx+1]; m&=z>sub
            if not m.any(): continue
            tu=w0*UV[a,0]+w1*UV[b,0]+w2*UV[c,0]; tv=w0*UV[a,1]+w1*UV[b,1]+w2*UV[c,1]
            px=np.clip((tu%1.0)*tw,0,tw-1).astype(int); py=np.clip((tv%1.0)*th,0,th-1).astype(int)
            col=tex[py,px]
            if tex is not glow_tex: m&=col[...,3]>100
            if not m.any(): continue
            nn=N[a]*w0[...,None]+N[b]*w1[...,None]+N[c]*w2[...,None]
            sh=0.45+0.55*np.abs((nn@L)/(np.linalg.norm(nn,axis=-1)+1e-9))
            out=col[...,:3]*sh[...,None]*light
            if tex is glow_tex: out=out+col[...,:3]*(col[...,3:4]/255.0)   # unlit additive pass
            img[miny:maxy+1,minx:maxx+1][m]=out[m]
            sub[m]=z[m]
    return Image.fromarray(np.clip(img,0,255).astype(np.uint8))

def render_layers(P, N, layers, W=400, H=600, view='front', bounds=None, light=1.0, bg=40):
    """layers: list of dict(tex, tris, uv, mode) with mode 'lit' (alpha-tested, z-write) or 'add' (unlit One/One, z-test only).
    Lit layers first, then additive ones, like the client's opaque/transparent split."""
    if view=='front': sx, sy, depth = -P[:,1], P[:,2], P[:,0]
    elif view=='side': sx, sy, depth = P[:,0], P[:,2], P[:,1]
    else: sx, sy, depth = P[:,1], P[:,2], -P[:,0]
    x0,x1,y0,y1=bounds; sc=min((W-20)/(x1-x0),(H-20)/(y1-y0))
    X=(sx-x0)*sc+10; Y=H-((sy-y0)*sc+10)
    img=np.full((H,W,3),bg,np.float32); zb=np.full((H,W),-1e9,np.float32)
    L=np.array([0.5,0.3,0.8]); L/=np.linalg.norm(L)
    for lay in sorted(layers, key=lambda l: l['mode']=='add'):
        tex=lay['tex']; UV=lay['uv']; th,tw=tex.shape[:2]; add=lay['mode']=='add'
        for a,b,c in lay['tris']:
            xs=X[[a,b,c]]; ys=Y[[a,b,c]]
            minx=max(int(xs.min()),0); maxx=min(int(xs.max())+1,W-1); miny=max(int(ys.min()),0); maxy=min(int(ys.max())+1,H-1)
            if minx>maxx or miny>maxy: continue
            d=(ys[1]-ys[2])*(xs[0]-xs[2])+(xs[2]-xs[1])*(ys[0]-ys[2])
            if abs(d)<1e-12: continue
            gx,gy=np.meshgrid(np.arange(minx,maxx+1)+0.5,np.arange(miny,maxy+1)+0.5)
            w0=((ys[1]-ys[2])*(gx-xs[2])+(xs[2]-xs[1])*(gy-ys[2]))/d
            w1=((ys[2]-ys[0])*(gx-xs[2])+(xs[0]-xs[2])*(gy-ys[2]))/d
            w2=1-w0-w1
            m=(w0>=0)&(w1>=0)&(w2>=0)
            if not m.any(): continue
            z=w0*depth[a]+w1*depth[b]+w2*depth[c]
            sub=zb[miny:maxy+1,minx:maxx+1]; m&=z>=sub-1e-4
            if not m.any(): continue
            tu=w0*UV[a,0]+w1*UV[b,0]+w2*UV[c,0]; tv=w0*UV[a,1]+w1*UV[b,1]+w2*UV[c,1]
            px=np.clip((tu%1.0)*tw,0,tw-1).astype(int); py=np.clip((tv%1.0)*th,0,th-1).astype(int)
            col=tex[py,px]
            reg=img[miny:maxy+1,minx:maxx+1]
            if add:
                reg[m]=(reg+col[...,:3])[m]
            else:
                m&=col[...,3]>100
                if not m.any(): continue
                nn=N[a]*w0[...,None]+N[b]*w1[...,None]+N[c]*w2[...,None]
                sh=0.45+0.55*np.abs((nn@L)/(np.linalg.norm(nn,axis=-1)+1e-9))
                reg[m]=(col[...,:3]*sh[...,None]*light)[m]
                sub[m]=z[m]
    return Image.fromarray(np.clip(img,0,255).astype(np.uint8))
