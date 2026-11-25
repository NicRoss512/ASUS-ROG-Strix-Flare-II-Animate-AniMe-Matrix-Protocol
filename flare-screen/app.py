#!/usr/bin/env python3
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
import sys
import json
import time
import threading
from dataclasses import asdict, replace
from pathlib import Path
import cv2
import numpy as np
import mss
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from core import Config, points, render, Transport

SETTINGS = Path.home()/'.config/flare-screen/settings.json'

class Worker(C.QThread):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.lock = threading.Lock()
        self.latest = None
        self.message = 'Готов'
        self.active = False
        self.reset = 0
    def configure(self, cfg):
        with self.lock:
            self.cfg = cfg
    def snapshot(self):
        with self.lock:
            return self.latest, self.message
    def run(self):
        transport = Transport()
        cap = None
        key = None
        previous = None
        count = 0
        start = time.monotonic()
        hid_retry = 0
        sent = False
        last_cfg = self.cfg
        try:
            with mss.mss() as screen:
                while not self.isInterruptionRequested():
                    tick = time.monotonic()
                    with self.lock:
                        cfg = replace(self.cfg)
                    last_cfg = cfg
                    if not self.active:
                        if sent:
                            try:
                                if cfg.clear_on_stop:
                                    transport.write(np.zeros(312,np.uint8))
                            except Exception as e:
                                self.message = str(e)
                            transport.close()
                            sent = False
                        self.msleep(80)
                        key = None
                        previous = None
                        continue
                    try:
                        newkey = (cfg.source,cfg.video,self.reset)
                        if newkey != key:
                            if cap is not None:
                                cap.release()
                                cap = None
                            previous = None
                            if cfg.source == 'Видео':
                                cap = cv2.VideoCapture(cfg.video)
                                if not cap.isOpened():
                                    raise RuntimeError('Не удалось открыть видео')
                                vf = cap.get(cv2.CAP_PROP_FPS)
                                vf = vf if 0 < vf < 1000 else 25
                                video_start = tick
                                last_target = -1
                            key = newkey
                        if cfg.source == 'Экран':
                            if cfg.region:
                                x,y,w,h = cfg.region
                                region = dict(left=x,top=y,width=w,height=h)
                            else:
                                if cfg.monitor >= len(screen.monitors):
                                    raise RuntimeError('Монитор не найден')
                                region = screen.monitors[cfg.monitor]
                            frame = np.asarray(screen.grab(region))[:,:,:3].copy()
                            values = render(frame,cfg)
                        elif cfg.source == 'Видео':
                            target = int((tick-video_start)*vf)
                            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                            if total > 0 and target >= total:
                                if not cfg.loop:
                                    self.active = False
                                    self.message = 'Видео завершено'
                                    continue
                                video_start = tick
                                target = 0
                            if target != last_target:
                                # Seek on skips/loops; sequential read in the usual case.
                                if target != last_target+1:
                                    cap.set(cv2.CAP_PROP_POS_FRAMES,target)
                                ok, image = cap.read()
                                if not ok:
                                    if cfg.loop:
                                        cap.set(cv2.CAP_PROP_POS_FRAMES,0)
                                        video_start = tick
                                        ok,image = cap.read()
                                    if not ok:
                                        self.active = False
                                        self.message = 'Конец видео или ошибка декодирования'
                                        continue
                                frame = image
                                last_target = target
                            values = render(frame,cfg)
                        else:
                            frame = np.zeros((180,320,3),np.uint8)
                            values = np.zeros(312,np.uint8)
                            level = round(cfg.brightness*255/100)
                            if cfg.source == 'Все LED':
                                values[:] = level
                            elif cfg.source == 'Шахматка':
                                from core import ORDER
                                values[:] = [level if (r+c)%2 else 0 for r,c in ORDER]
                            elif cfg.source == 'Один LED':
                                values[cfg.raw_index] = level
                            elif cfg.source == 'Сканирование':
                                values[int(tick*5)%312] = level
                        if previous is not None:
                            values = np.rint(previous*cfg.smooth+values*(1-cfg.smooth)).astype(np.uint8)
                        previous = values.copy()
                        status = 'Предпросмотр'
                        if cfg.send:
                            if tick >= hid_retry:
                                try:
                                    transport.write(values)
                                    sent = True
                                    status = 'HID подключён'
                                except Exception as e:
                                    transport.close()
                                    hid_retry = tick+2
                                    self.hid_error = str(e)
                                    status = self.hid_error
                            else:
                                status = getattr(self,'hid_error','Ожидание HID')
                        elif sent:
                            if cfg.clear_on_stop:
                                transport.write(np.zeros(312,np.uint8))
                            transport.close()
                            sent = False
                        # Downsize before placing a single latest frame in the mailbox.
                        thumb = cv2.resize(frame,(480,270),interpolation=cv2.INTER_AREA)
                        count += 1
                        elapsed = time.monotonic()-start
                        if elapsed >= 1:
                            self.rate = count/elapsed
                            count = 0
                            start = time.monotonic()
                        with self.lock:
                            self.latest = (thumb,values,cfg)
                            self.message = f'{status} · {getattr(self,"rate",0):.1f} FPS'
                    except Exception as e:
                        self.message = str(e)
                        key = None
                        self.msleep(500)
                    remaining = 1/max(1,cfg.fps)-(time.monotonic()-tick)
                    if remaining > 0:
                        self.msleep(max(1,int(remaining*1000)))
        except Exception as e:
            self.message = f'Ошибка захвата X11: {e}'
        finally:
            if cap is not None:
                cap.release()
            try:
                if sent and last_cfg.clear_on_stop:
                    transport.write(np.zeros(312,np.uint8))
            except Exception:
                pass
            transport.close()

class Matrix(W.QWidget):
    def __init__(self):
        super().__init__()
        self.values = np.zeros(312,np.uint8)
        self.cfg = Config()
        self.setMinimumSize(480,290)
    def paintEvent(self,event):
        qp = G.QPainter(self)
        qp.fillRect(self.rect(),G.QColor('#101319'))
        qp.setRenderHint(G.QPainter.RenderHint.Antialiasing)
        pts = points(self.cfg)
        scale = min((self.width()-40)/(pts[:,0].max()+1),(self.height()-40)/(pts[:,1].max()+self.cfg.row_pitch))
        ox = (self.width()-(pts[:,0].max()+1)*scale)/2
        oy = (self.height()-(pts[:,1].max()+self.cfg.row_pitch)*scale)/2
        qp.setPen(C.Qt.PenStyle.NoPen)
        for (x,y),v in zip(pts,self.values):
            # Display actual byte values, not auto-normalized brightness.
            v = int(v)
            qp.setBrush(G.QColor(max(16,v),max(19,v),max(24,v)))
            qp.drawEllipse(C.QPointF(ox+(x+.5)*scale,oy+(y+self.cfg.row_pitch/2)*scale),scale*.19,scale*.19)

class Selector(W.QWidget):
    chosen = C.Signal(tuple)
    def __init__(self):
        super().__init__(None,C.Qt.WindowType.FramelessWindowHint | C.Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose)
        rect = C.QRect()
        for s in W.QApplication.screens():
            rect = rect.united(s.geometry())
        self.setGeometry(rect)
        self.setWindowOpacity(.35)
        self.setCursor(C.Qt.CursorShape.CrossCursor)
        self.a = self.b = None
    def paintEvent(self,e):
        p = G.QPainter(self)
        p.fillRect(self.rect(),G.QColor('#152538'))
        p.setPen(G.QPen(G.QColor('#ffffff'),2))
        p.drawText(30,40,'Выдели область мышью. Esc — отмена.')
        if self.a is not None:
            p.drawRect(C.QRect(self.a,self.b).normalized())
    def mousePressEvent(self,e):
        self.a = self.b = e.position().toPoint()
        self.update()
    def mouseMoveEvent(self,e):
        if self.a is not None:
            self.b = e.position().toPoint()
            self.update()
    def mouseReleaseEvent(self,e):
        if self.a is not None:
            r = C.QRect(self.a,e.position().toPoint()).normalized()
            if r.width()>1 and r.height()>1:
                pos = self.mapToGlobal(r.topLeft())
                # X11 native coordinates; force Qt scaling off in launcher.
                self.chosen.emit((pos.x(),pos.y(),r.width(),r.height()))
        self.close()
    def keyPressEvent(self,e):
        if e.key() == C.Qt.Key.Key_Escape:
            self.close()

class Window(W.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Flare Screen · AniMe Matrix')
        self.resize(1100,820)
        self.controls = {}
        root = W.QWidget()
        self.setCentralWidget(root)
        layout = W.QHBoxLayout(root)
        panel = W.QWidget()
        form = W.QFormLayout(panel)
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(365)
        layout.addWidget(scroll,0)
        right = W.QVBoxLayout()
        layout.addLayout(right,1)
        right.addWidget(W.QLabel('Источник (предпросмотр растянут до 16:9)'))
        self.image = W.QLabel()
        self.image.setMinimumSize(480,270)
        self.image.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.image)
        right.addWidget(W.QLabel('312 LED · физическая раскладка · реальная яркость байтов'))
        self.matrix = Matrix()
        right.addWidget(self.matrix,1)
        self.status = W.QLabel('Готов — отправка на устройство выключена')
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        def combo(name,label,items):
            w = W.QComboBox(); w.addItems(items)
            self.controls[name] = w; form.addRow(label,w)
        def spin(name,label,lo,hi,default,step=1):
            w = W.QDoubleSpinBox() if isinstance(default,float) else W.QSpinBox()
            w.setRange(lo,hi); w.setSingleStep(step); w.setValue(default)
            self.controls[name] = w; form.addRow(label,w)
        def check(name,label,default=False):
            w = W.QCheckBox(label); w.setChecked(default)
            self.controls[name] = w; form.addRow(w)
        combo('source','Источник',['Экран','Видео','Все LED','Шахматка','Один LED','Сканирование','Погасить'])
        with mss.mss() as s:
            monitors = s.monitors[1:]
        self.monitor = W.QComboBox()
        for i,m in enumerate(monitors,1):
            self.monitor.addItem(f'{i}: {m["width"]}×{m["height"]} ({m["left"]}, {m["top"]})',i)
        form.addRow('Монитор',self.monitor)
        self.region = W.QLineEdit(); self.region.setPlaceholderText('x,y,width,height; пусто = монитор')
        form.addRow('Область',self.region)
        pick = W.QPushButton('Выделить область мышью')
        pick.clicked.connect(self.select_region); form.addRow(pick)
        self.video = W.QLineEdit(); form.addRow('Видеофайл',self.video)
        browse = W.QPushButton('Открыть видео…'); browse.clicked.connect(self.open_video); form.addRow(browse)
        check('loop','Повтор видео',True)
        combo('fit','Масштабирование',['Заполнить','Вписать','Растянуть'])
        spin('fps','Частота FPS',1,60,15)
        spin('brightness','Яркость %',0,100,25)
        spin('contrast','Контраст',.1,4.,1.0,.1)
        spin('gamma','Гамма',.1,4.,1.0,.1)
        spin('blur','Радиус усреднения (шаг LED)',0.,2.,.65,.05)
        spin('smooth','Сглаживание по времени',0.,.95,0.0,.05)
        check('invert','Инверсия'); check('mirror','Отражение по горизонтали')
        combo('rotation','Поворот',['0','90','180','270'])
        spin('row_pitch','Высота ряда / шаг LED',.1,2.,.5,.05)
        spin('shear','Сдвиг ряда / шаг LED',0.,1.,.5,.01)
        spin('raw_index','Индекс LED (0–311)',0,311,0)
        check('send','Отправлять на клавиатуру',False)
        check('clear_on_stop','Гасить при остановке',True)
        self.go = W.QPushButton('▶ Старт'); self.go.clicked.connect(self.toggle); form.addRow(self.go)
        save = W.QPushButton('Сохранить настройки'); save.clicked.connect(self.save); form.addRow(save)
        form.addRow(W.QLabel('Видео: пауза реализуется остановкой;\nследующий старт — с начала.'))
        self.load()
        self.worker = Worker(self.config())
        self.worker.start()
        self.timer = C.QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(66)
    def config(self):
        d = {}
        for name,w in self.controls.items():
            d[name] = w.isChecked() if isinstance(w,W.QCheckBox) else w.currentText() if isinstance(w,W.QComboBox) else w.value()
        d['rotation'] = int(d['rotation'])
        d['monitor'] = self.monitor.currentData() or 1
        text = self.region.text().strip()
        d['region'] = None
        if text:
            r = tuple(map(int,text.split(',')))
            if len(r)!=4 or r[2]<=0 or r[3]<=0:
                raise ValueError('Область: x,y,width,height; размеры должны быть > 0')
            d['region'] = r
        d['video'] = self.video.text()
        return Config(**d)
    def toggle(self):
        try:
            cfg = self.config()
            self.worker.configure(cfg)
            self.worker.active = not self.worker.active
        except Exception as e:
            W.QMessageBox.warning(self,'Настройки',str(e))
    def refresh(self):
        try:
            self.worker.configure(self.config())
        except Exception as e:
            self.status.setText(str(e)); return
        latest,msg = self.worker.snapshot()
        self.status.setText(msg)
        self.go.setText('■ Остановить' if self.worker.active else '▶ Старт')
        if latest:
            frame,values,cfg = latest
            rgb = cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
            image = G.QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],G.QImage.Format.Format_RGB888).copy()
            self.image.setPixmap(G.QPixmap.fromImage(image))
            self.matrix.values = values; self.matrix.cfg = cfg; self.matrix.update()
    def select_region(self):
        self.selector = Selector()
        self.selector.chosen.connect(lambda r:self.region.setText(','.join(map(str,r))))
        self.selector.show()
    def open_video(self):
        path,_ = W.QFileDialog.getOpenFileName(self,'Видеофайл','','Видео (*.mp4 *.mkv *.avi *.webm *.mov);;Все файлы (*)')
        if path:
            self.video.setText(path); self.controls['source'].setCurrentText('Видео')
    def save(self):
        try:
            cfg = asdict(self.config()); cfg['send'] = False
            SETTINGS.parent.mkdir(parents=True,exist_ok=True)
            SETTINGS.write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
            self.status.setText(f'Сохранено: {SETTINGS}')
        except Exception as e:
            W.QMessageBox.warning(self,'Сохранение',str(e))
    def load(self):
        try:
            d = json.loads(SETTINGS.read_text())
            for name,w in self.controls.items():
                if name not in d or name == 'send': continue
                if isinstance(w,W.QCheckBox): w.setChecked(d[name])
                elif isinstance(w,W.QComboBox): w.setCurrentText(str(d[name]))
                else: w.setValue(d[name])
            if d.get('region'): self.region.setText(','.join(map(str,d['region'])))
            self.video.setText(d.get('video',''))
            self.monitor.setCurrentIndex(max(0,self.monitor.findData(d.get('monitor',1))))
        except (OSError,ValueError,TypeError):
            pass
    def closeEvent(self,e):
        self.worker.requestInterruption()
        if not self.worker.wait(4000):
            self.status.setText('Ожидаю завершения операции захвата/HID…')
            e.ignore()
            return
        e.accept()

if __name__ == '__main__':
    app = W.QApplication(sys.argv)
    app.setStyle('Fusion')
    win = Window(); win.show()
    sys.exit(app.exec())
