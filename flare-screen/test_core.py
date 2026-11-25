import unittest
import numpy as np
from core import ORDER, Config, packet, render

class Tests(unittest.TestCase):
    def test_mapping(self):
        expected = {0:(0,0),1:(1,0),2:(0,1),11:(1,5),61:(2,12),111:(6,3),161:(9,12),211:(13,9),261:(18,0),291:(21,5),310:(23,6),311:(22,7)}
        self.assertEqual(len(set(ORDER)),312)
        for i,p in expected.items(): self.assertEqual(ORDER[i],p)
    def test_packet(self):
        p = packet(np.full(312,255,np.uint8))
        self.assertEqual(len(p),1024)
        self.assertEqual(p[:4],b'\x60\x81\0\0')
        self.assertEqual(p[4:316],bytes([255])*312)
        self.assertEqual(p[316:],bytes(708))
    def test_white_black(self):
        c = Config(brightness=100)
        for value in (0,255):
            a = render(np.full((120,200,3),value,np.uint8),c)
            self.assertEqual(a.shape,(312,))
            self.assertTrue(np.all(a==value))
    def test_controls(self):
        c = Config(brightness=25,invert=True)
        a = render(np.zeros((70,110,3),np.uint8),c)
        self.assertTrue(np.all(a==64))
        for fit in ('Заполнить','Вписать','Растянуть'):
            for rotation in (0,90,180,270):
                a=render(np.zeros((73,141,3),np.uint8),Config(fit=fit,rotation=rotation))
                self.assertEqual(a.shape,(312,))
    def test_gradient(self):
        g=np.tile(np.linspace(0,255,400,dtype=np.uint8),(200,1))
        a=render(np.repeat(g[:,:,None],3,axis=2),Config(brightness=100,fit='Растянуть'))
        self.assertLess(a[0],a[36])

if __name__ == '__main__': unittest.main()
