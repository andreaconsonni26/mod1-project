"""Passo 1 - Una finestra bianca e vuota.

Prova:  python esempi/passo1_finestra.py
"""
import sys

from PySide6.QtWidgets import QApplication, QMainWindow

app = QApplication(sys.argv)  # 1. l'applicazione Qt: una sola per programma, va creata per prima

finestra = QMainWindow()  # 2. la finestra principale
finestra.setWindowTitle("La mia app")
finestra.resize(900, 600)  # larghezza x altezza in pixel
finestra.setStyleSheet("background: white;")  # sfondo bianco

finestra.show()  # 3. rendila visibile
sys.exit(app.exec())  # 4. "ciclo degli eventi": il programma resta qui finché chiudi la finestra
