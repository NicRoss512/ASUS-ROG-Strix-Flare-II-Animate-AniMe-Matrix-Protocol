#!/usr/bin/env python3
from __future__ import annotations
import struct, sys
from collections import Counter, defaultdict
from pathlib import Path

# USBPcap basics
# base header 27B + optional 1B control stage (for transfer type 2)

class Rec:
    __slots__ = ('idx','ts','caplen','origlen','hlen','irp','status','function','info','bus','dev','ep','transfer','datalen','stage','payload','raw')
    def __init__(self, idx, ts, caplen, origlen, data):
        self.idx=idx; self.ts=ts; self.caplen=caplen; self.origlen=origlen; self.raw=data
        self.hlen=None; self.irp=None; self.status=None; self.function=None; self.info=None; self.bus=None; self.dev=None; self.ep=None; self.transfer=None; self.datalen=None; self.stage=None; self.payload=b''
        if len(data)>=27:
            self.hlen=struct.unpack_from('<H', data, 0)[0]
            self.irp=struct.unpack_from('<Q', data, 2)[0]
            self.status=struct.unpack_from('<I', data, 10)[0]
            self.function=struct.unpack_from('<H', data, 14)[0]
            self.info=data[16]
            self.bus=struct.unpack_from('<H', data, 17)[0]
            self.dev=struct.unpack_from('<H', data, 19)[0]
            self.ep=data[21]
            self.transfer=data[22]
            self.datalen=struct.unpack_from('<I', data, 23)[0]
            if self.transfer==2 and self.hlen and self.hlen>27 and len(data)>=28:
                self.stage=data[27]
            if self.hlen is not None and 0 <= self.hlen <= len(data):
                self.payload=data[self.hlen:]

def read_pcap(path):
    b=Path(path).read_bytes()
    if len(b)<24: raise ValueError('short pcap')
    magic=b[:4]
    if magic==b'\xd4\xc3\xb2\xa1': endian='<'
    elif magic==b'\xa1\xb2\xc3\xd4': endian='>'
    else: raise ValueError(f'bad pcap magic {magic.hex()}')
    gh=struct.unpack_from(endian+'IHHIIII', b, 0)
    linktype=gh[-1]
    print(f'pcap endian={endian} linktype={linktype} snaplen={gh[-2]}')
    off=24; idx=0
    while off+16<=len(b):
        ts_sec, ts_usec, incl, orig=struct.unpack_from(endian+'IIII', b, off); off+=16
        data=b[off:off+incl]; off+=incl
        yield Rec(idx, ts_sec+ts_usec/1_000_000, incl, orig, data)
        idx+=1

def hexs(x, n=32):
    return ' '.join(f'{i:02x}' for i in x[:n]) + (' ...' if len(x)>n else '')

def parse_dev_desc(p):
    if len(p)>=18 and p[0]==18 and p[1]==1:
        return dict(vid=struct.unpack_from('<H',p,8)[0], pid=struct.unpack_from('<H',p,10)[0], bcd=struct.unpack_from('<H',p,12)[0], cls=p[4], sub=p[5], proto=p[6], maxpk=p[7], configs=p[17])

def parse_cfg_descs(p):
    descs=[]; off=0
    while off+2<=len(p):
        l=p[off]; t=p[off+1]
        if l<2 or off+l>len(p): break
        descs.append((off,t,p[off:off+l]))
        off += l
    return descs

def main():
    path=sys.argv[1] if len(sys.argv)>1 else '/home/user/usbcap/USB.cap'
    recs=list(read_pcap(path))
    print('records', len(recs), 'duration', (recs[-1].ts-recs[0].ts if recs else 0))
    cnt=Counter((r.bus,r.dev,r.ep,r.transfer,r.info,r.function) for r in recs)
    print('top bus/dev/ep/transfer/info/function:')
    for k,v in cnt.most_common(30): print(v,k)
    print('\nDevice descriptors:')
    dev_vidpid={}
    for r in recs:
        d=parse_dev_desc(r.payload)
        if d:
            dev_vidpid[(r.bus,r.dev)] = (d['vid'],d['pid'])
            print(f'#{r.idx} bus{r.bus} dev{r.dev} ep{r.ep:02x} info{r.info} fn{r.function:04x} stage{r.stage} VID:PID={d["vid"]:04x}:{d["pid"]:04x} bcd={d["bcd"]:04x} cls={d["cls"]:02x} maxpk={d["maxpk"]}')
    print('\nConfig/interface descriptors containing iface/endpoint by device:')
    for r in recs:
        if len(r.payload)>=9 and r.payload[0]==9 and r.payload[1]==2:
            vidpid=dev_vidpid.get((r.bus,r.dev))
            print(f'#{r.idx} bus{r.bus} dev{r.dev} vidpid={vidpid} cfg payload len={len(r.payload)} total={struct.unpack_from("<H",r.payload,2)[0]}')
            for off,t,d in parse_cfg_descs(r.payload):
                if t==4 and len(d)>=9:
                    print(f'  intf off{off}: num={d[2]} alt={d[3]} eps={d[4]} class={d[5]:02x} sub={d[6]:02x} proto={d[7]:02x}')
                elif t==5 and len(d)>=7:
                    print(f'  ep   off{off}: addr=0x{d[2]:02x} attr=0x{d[3]:02x} max={struct.unpack_from("<H",d,4)[0]} int={d[6]}')
                elif t==0x21 and len(d)>=9:
                    print(f'  hid  off{off}: ver={struct.unpack_from("<H",d,2)[0]:04x} report_len={struct.unpack_from("<H",d,7)[0]}')
    print('\nNon-empty payload records for ASUS 0b05:19fc or unknown large endpoints:')
    for r in recs:
        vidpid=dev_vidpid.get((r.bus,r.dev))
        interesting = vidpid==(0x0b05,0x19fc) or (r.datalen and r.datalen>=16 and r.dev not in [1,2,3])
        if interesting and len(r.payload)>0:
            print(f'#{r.idx} t={r.ts-recs[0].ts:.6f} bus{r.bus} dev{r.dev} vidpid={vidpid} ep=0x{r.ep:02x} xfer={r.transfer} info={r.info} fn=0x{r.function:04x} status=0x{r.status:08x} hlen={r.hlen} stage={r.stage} datalen={r.datalen} cap_payload={len(r.payload)} {hexs(r.payload,64)}')

if __name__=='__main__': main()
