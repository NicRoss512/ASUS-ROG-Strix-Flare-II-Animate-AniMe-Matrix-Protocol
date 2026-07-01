#!/usr/bin/env python3
"""
ROG Strix Flare II Animate AniMe Matrix clock, protocol from USBPcap.

Writes 1024-byte frames to HID interface 4 (hidraw5), payload starts with
60 81. No Report-ID prefix, no control transfers.

This version has a few render presets because the panel is diagonal and ASUS
uses a pre-distorted font. Try the presets below and keep the one that looks
best on the real keyboard.
"""
from __future__ import annotations
import argparse, datetime as dt, time

VID=0x0B05
PID=0x19FC
IFACE=4
FRAME_SIZE=1024
PREFIX=bytes([0x60,0x81])
FB_OFFSET=15
STRIDE=32

FONT5={
 '0':["11011","10001","10001","10001","01010"],
 '1':["00110","00010","00010","00010","00010"],
 '2':["01011","00001","11010","10000","01010"],
 '3':["01011","00001","01011","00001","01010"],
 '4':["10001","10001","01011","00001","00000"],
 '5':["11010","10000","01011","00001","01010"],
 '6':["11011","10000","11011","10001","01010"],
 '7':["010111","00001","00010","00010","00010"],
 '8':["11011","10001","11011","10001","01010"],
 '9':["11011","10001","01011","00001","01010"],
}

FONT3={
 '0':["101","101","101","101","101"],
 '1':["001","001","001","001","001"],
 '2':["101","001","101","100","101"],
 '3':["101","001","101","001","101"],
 '4':["101","101","101","001","001"],
 '5':["101","100","101","001","101"],
 '6':["101","100","101","101","101"],
 '7':["101","001","001","001","001"],
 '8':["101","101","101","111","101"],
 '9':["101","101","101","001","101"],
}

PRESETS={
    # Original simple layout, kept for comparison.
    'old':      dict(font='5', xs=[0,6,18,24], colon_x=15, shear=0, colon_fixed=True),
    # Matches the X positions visible in the Armoury 02:43 capture top row:
    # 0 at x=0, 2 at x=6, 4 at x=14, 3 at x=20; colon blink at x=15.
    'capture':  dict(font='5', xs=[0,6,14,20], colon_x=15, shear=0, colon_fixed=True),
    # Same, but each glyph row is shifted right by 3 logical columns. This often
    # compensates the physical diagonal slant.
    'shear3':   dict(font='5', xs=[0,6,14,20], colon_x=15, shear=3, colon_fixed=True),
    # Curved row shifts: first two rows keep your best shear=2, lower rows drift less.
    'curve1':   dict(font='5', xs=[0,6,14,20], colon_x=15, shear=2, row_shifts=[0,2,3,4,5], colon_fixed=True),
    'curve2':   dict(font='5', xs=[0,6,14,20], colon_x=15, shear=2, row_shifts=[0,2,2,3,4], colon_fixed=True),
    'curve3':   dict(font='5', xs=[0,6,14,20], colon_x=15, shear=2, row_shifts=[0,2,2,2,3], colon_fixed=True),
    # Best readable variant found on the real ROG Strix Flare II Animate.
    'flare':    dict(font='5', xs=[0,6,14,20], colon_x=15, shear=2, row_shifts=[0,3,4,3,0], colon_fixed=True),
    # Compact font: less overlap on the diagonal panel.
    'compact':  dict(font='3', xs=[0,4,10,14], colon_x=8, shear=0, colon_fixed=True),
    'compact2': dict(font='3', xs=[0,4,10,14], colon_x=8, shear=2, colon_fixed=True),
}

def open_iface(iface:int):
    import hid  # type: ignore
    matches=[d for d in hid.enumerate(VID,PID) if d.get('interface_number')==iface]
    if not matches:
        raise RuntimeError(f'interface {iface} not found')
    h=hid.device(); h.open_path(matches[0]['path'])
    print(f"opened iface={iface}, path={matches[0]['path']!r}")
    return h

def setpx(frame:bytearray,x:int,y:int,val:int):
    if 0 <= x < STRIDE and 0 <= y:
        off=FB_OFFSET + y*STRIDE + x
        if off < len(frame):
            frame[off]=val

def row_shift(gy:int, shear:int, row_shifts):
    if row_shifts is not None and gy < len(row_shifts):
        return int(row_shifts[gy])
    return int(shear) * gy

def draw_glyph(frame:bytearray, font:dict, ch:str, x:int, y:int, val:int, shear:int, row_shifts=None):
    g=font.get(ch)
    if not g: return
    for gy,row in enumerate(g):
        sx=x + row_shift(gy, shear, row_shifts)
        for gx,c in enumerate(row):
            if c=='1': setpx(frame, sx+gx, y+gy, val)

def make_frame(text:str, *, preset:str, val:int, y:int, blink_colon:bool, overrides:dict)->bytes:
    cfg=dict(PRESETS[preset])
    cfg.update({k:v for k,v in overrides.items() if v is not None})
    row_shifts = cfg.get('row_shifts')
    font=FONT5 if str(cfg['font'])=='5' else FONT3
    frame=bytearray(FRAME_SIZE); frame[0:2]=PREFIX
    if len(text) < 5: text=text.rjust(5)
    xs=cfg['xs']
    draw_glyph(frame,font,text[0],xs[0],y,val,int(cfg['shear']),row_shifts)
    draw_glyph(frame,font,text[1],xs[1],y,val,int(cfg['shear']),row_shifts)
    # Colon: by default keep it at a fixed x. In the capture it blinks at
    # absolute logical x=15, rows 1 and 3.
    if blink_colon:
        cx=int(cfg['colon_x'])
        if cfg.get('colon_fixed', True):
            setpx(frame,cx,y+1,val); setpx(frame,cx,y+3,val)
        else:
            setpx(frame,cx+row_shift(1,int(cfg['shear']),row_shifts),y+1,val)
            setpx(frame,cx+row_shift(3,int(cfg['shear']),row_shifts),y+3,val)
    draw_glyph(frame,font,text[3],xs[2],y,val,int(cfg['shear']),row_shifts)
    draw_glyph(frame,font,text[4],xs[3],y,val,int(cfg['shear']),row_shifts)
    return bytes(frame)

def write_frame(h,frame:bytes,verbose=False):
    n=h.write(frame)  # exactly 1024 bytes, no report-id prefix
    if verbose: print('write',n,frame[:40].hex(' '))
    if n<0:
        try: err=h.error()
        except Exception: err=''
        raise OSError(f'hid.write failed: {err}')

def clamp_u8(v:int)->int:
    return max(0, min(255, int(v)))

def brightness_to_raw(percent:int)->int:
    # User-facing brightness is 0..100%, protocol pixel brightness is 0..255.
    percent=max(0, min(100, int(percent)))
    return round(percent * 255 / 100)

def get_brightness_value(args)->int:
    # Priority:
    #   --raw-brightness / --pixel: exact protocol value 0..255
    #   --brightness: percentage 0..100
    if args.raw_brightness is not None:
        return clamp_u8(args.raw_brightness)
    if args.pixel is not None:
        return clamp_u8(args.pixel)
    return brightness_to_raw(args.brightness)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--iface',type=int,default=IFACE)
    ap.add_argument('--preset',choices=sorted(PRESETS),default='flare')
    ap.add_argument('--list-presets',action='store_true')
    ap.add_argument('--text',default='',help='override time, e.g. 12:34')
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--clear',action='store_true')
    ap.add_argument('--brightness','-b',type=lambda s:int(s,0),default=35,
                    help='LED brightness in percent 0..100, default 35')
    ap.add_argument('--raw-brightness',type=lambda s:int(s,0),default=None,
                    help='raw LED brightness 0..255; overrides --brightness')
    ap.add_argument('--pixel',type=lambda s:int(s,0),default=None,
                    help='deprecated alias for --raw-brightness, 0..255')
    ap.add_argument('--y',type=int,default=0)
    ap.add_argument('--no-blink',action='store_true')
    ap.add_argument('--period',type=float,default=0.5)
    # overrides for quick tuning
    ap.add_argument('--shear',type=int,default=None)
    ap.add_argument('--colon-x',type=int,default=None)
    ap.add_argument('--font',choices=['3','5'],default=None)
    ap.add_argument('--xs',default=None,help='comma-separated x positions for 4 digits, e.g. 0,6,14,20')
    ap.add_argument('--row-shifts',default=None,help='comma-separated per-glyph-row x shifts, e.g. 0,2,3,4,5')
    ap.add_argument('--verbose','-v',action='store_true')
    args=ap.parse_args()
    if args.list_presets:
        for k,v in PRESETS.items(): print(k,v)
        return
    overrides={'shear':args.shear, 'colon_x':args.colon_x, 'font':args.font}
    if args.row_shifts:
        rs=[int(x) for x in args.row_shifts.split(',')]
        if len(rs) < 5: raise SystemExit('--row-shifts needs at least 5 numbers for font5')
        overrides['row_shifts']=rs
    if args.xs:
        xs=[int(x) for x in args.xs.split(',')]
        if len(xs)!=4: raise SystemExit('--xs needs exactly 4 numbers')
        overrides['xs']=xs
    h=open_iface(args.iface)
    try:
        if args.clear:
            write_frame(h,PREFIX+b'\x00'*(FRAME_SIZE-2),args.verbose); return
        while True:
            now=dt.datetime.now()
            text=args.text or now.strftime('%H:%M')
            colon=True if args.no_blink else (now.second%2==0)
            frame=make_frame(text,preset=args.preset,val=get_brightness_value(args),y=args.y,blink_colon=colon,overrides=overrides)
            write_frame(h,frame,args.verbose)
            if args.once: return
            time.sleep(args.period)
    except KeyboardInterrupt:
        print('\nstopped')
    finally:
        h.close()

if __name__=='__main__': main()
