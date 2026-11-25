"""Frame processing independent of Qt, capture and HID."""
from dataclasses import dataclass
import cv2
import numpy as np

COUNTS = [19,18,18,17,17,16,16,15,15,14,14,13,13,12,12,11,11,10,10,9,9,8,8,7]

def build_order():
    order = []
    for pair in range(12):
        for x in range(19):
            for row in (pair*2+1, pair*2):
                col = x - (row+1)//2
                if 0 <= col < COUNTS[row]:
                    order.append((row, col))
    assert len(order) == len(set(order)) == 312
    return order

ORDER = build_order()

@dataclass
class Config:
    source: str = 'Экран'
    monitor: int = 1
    region: tuple | None = None
    video: str = ''
    loop: bool = True
    fps: int = 15
    brightness: int = 25
    contrast: float = 1.0
    gamma: float = 1.0
    blur: float = 0.65
    smooth: float = 0.0
    fit: str = 'Заполнить'
    invert: bool = False
    mirror: bool = False
    rotation: int = 0
    row_pitch: float = 0.5
    shear: float = 0.5
    send: bool = False
    clear_on_stop: bool = True
    raw_index: int = 0


def points(cfg):
    # Half-pitch stagger; row height is configurable pending hardware calibration.
    return np.array([(c + r*cfg.shear, r*cfg.row_pitch) for r,c in ORDER], np.float32)


def packet(values):
    a = np.asarray(values, dtype=np.uint8)
    if a.shape != (312,):
        raise ValueError('Expected 312 LED values')
    return b'\x60\x81\x00\x00' + a.tobytes() + bytes(708)


def render(bgr, cfg):
    if cfg.rotation:
        bgr = np.rot90(bgr, -(cfg.rotation//90)).copy()
    if cfg.mirror:
        bgr = cv2.flip(bgr, 1)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)/255
    p = points(cfg)
    # Include half a cell around the LED centres.
    w = float(p[:,0].max()+1)
    h = float(p[:,1].max()+cfg.row_pitch)
    tw, th = max(1, round(w*16)), max(1, round(h*16))
    sh, sw = gray.shape
    if cfg.fit == 'Растянуть':
        canvas = cv2.resize(gray, (tw,th), interpolation=cv2.INTER_AREA)
    else:
        scale = (max if cfg.fit == 'Заполнить' else min)(tw/sw, th/sh)
        nw, nh = max(1,round(sw*scale)), max(1,round(sh*scale))
        resized = cv2.resize(gray, (nw,nh), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((th,tw), np.float32)
        dx,dy = (tw-nw)//2,(th-nh)//2
        x,y = max(0,dx),max(0,dy)
        sx,sy = max(0,-dx),max(0,-dy)
        cw,ch = min(tw-x,nw-sx),min(th-y,nh-sy)
        canvas[y:y+ch,x:x+cw] = resized[sy:sy+ch,sx:sx+cw]
    if cfg.blur > 0:
        canvas = cv2.GaussianBlur(canvas, (0,0), cfg.blur*16)
    mx = ((p[:,0]+.5)/w*(tw-1)).reshape(1,-1).astype(np.float32)
    my = ((p[:,1]+cfg.row_pitch/2)/h*(th-1)).reshape(1,-1).astype(np.float32)
    a = cv2.remap(canvas,mx,my,cv2.INTER_LINEAR).ravel()
    a = np.clip((a-.5)*cfg.contrast+.5,0,1)
    if cfg.invert:
        a = 1-a
    a = a**(1/max(.1,cfg.gamma))
    return np.rint(a*255*cfg.brightness/100).astype(np.uint8)


class Transport:
    def __init__(self):
        self.h = None
    def write(self, values):
        if self.h is None:
            import hid
            devices = [d for d in hid.enumerate(0x0b05,0x19fc) if d.get('interface_number') == 4]
            if not devices:
                raise RuntimeError('HID interface 4 не найден; проверь подключение и udev')
            self.h = hid.device()
            self.h.open_path(devices[0]['path'])
        n = self.h.write(packet(values))
        if n != 1024:
            raise IOError(f'HID: записано {n} из 1024 байт')
    def close(self):
        if self.h is not None:
            self.h.close()
            self.h = None
