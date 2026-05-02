import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QStatusBar, QLabel, QPushButton, QVBoxLayout, QWidget, QDialog
from PyQt6.QtCore import Qt, QTimer

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl = QLabel("Hidden")
        self.lbl.hide()
        sb.addPermanentWidget(self.lbl)
        
        w = QWidget()
        l = QVBoxLayout(w)
        b = QPushButton("Open")
        b.clicked.connect(self.open_dlg)
        l.addWidget(b)
        self.setCentralWidget(w)
        
        self.dlg = QDialog(self)
        self.dlg.setModal(True)
        l2 = QVBoxLayout(self.dlg)
        b2 = QPushButton("Hide & Count")
        l2.addWidget(b2)
        b2.clicked.connect(self.start_count)
        
        self.c = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        
    def open_dlg(self):
        self.dlg.show()
        
    def start_count(self):
        self.dlg.hide()
        self.c = 0
        self.timer.start(500)
        
    def tick(self):
        self.c += 1
        self.lbl.setText(f"Count: {self.c}")
        if not self.dlg.isVisible():
            self.lbl.show()

app = QApplication(sys.argv)
m = Main()
m.show()
QTimer.singleShot(1000, m.open_dlg)
QTimer.singleShot(2000, m.start_count)
QTimer.singleShot(5000, app.quit)
app.exec()
