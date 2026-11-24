"""Passo 6 - La finestra con le pagine: login -> codice -> pagina bianca.

Prova:  prima crea l'utente con  python esempi/passo5_login.py
        poi lancia               python esempi/passo6_finestra_login.py
(lancia entrambi dalla stessa cartella: usano lo stesso file prova_login.db)
"""
import sys

from PySide6.QtWidgets import (QApplication, QLabel, QLineEdit, QMainWindow, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from passo5_login import livello1, livello2  # riusiamo le funzioni del passo 5


class Finestra(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Login a due livelli")
        self.resize(900, 600)
        self.segreto = None  # verrà riempito dopo il livello 1

        # --- pagina 1: username e password
        self.pagina_login = QWidget()
        self.username = QLineEdit(placeholderText="Username")
        self.password = QLineEdit(placeholderText="Password", echoMode=QLineEdit.EchoMode.Password)
        self.esito_login = QLabel("")
        bottone_login = QPushButton("Continua")
        bottone_login.clicked.connect(self.controlla_password)  # segnale "clicked" -> nostra funzione
        colonna = QVBoxLayout(self.pagina_login)  # mette i widget uno sotto l'altro
        for widget in (QLabel("Livello 1: username e password"), self.username, self.password,
                       bottone_login, self.esito_login):
            colonna.addWidget(widget)

        # --- pagina 2: codice a 6 cifre
        self.pagina_codice = QWidget()
        self.codice = QLineEdit(placeholderText="Codice a 6 cifre", maxLength=6)
        self.esito_codice = QLabel("")
        bottone_codice = QPushButton("Verifica")
        bottone_codice.clicked.connect(self.controlla_codice)
        colonna = QVBoxLayout(self.pagina_codice)
        for widget in (QLabel("Livello 2: codice dall'app di autenticazione"), self.codice,
                       bottone_codice, self.esito_codice):
            colonna.addWidget(widget)

        # --- pagina 3: la finestra bianca, vuota
        self.pagina_principale = QWidget()
        self.pagina_principale.setStyleSheet("background: white;")

        # Le tre pagine stanno una sopra l'altra: se ne vede una alla volta
        self.pagine = QStackedWidget()
        for pagina in (self.pagina_login, self.pagina_codice, self.pagina_principale):
            self.pagine.addWidget(pagina)
        self.setCentralWidget(self.pagine)

    def controlla_password(self):
        self.segreto = livello1(self.username.text(), self.password.text())
        self.password.clear()  # non teniamo la password più del necessario
        if self.segreto is None:
            self.esito_login.setText("Username o password sbagliati")
        else:
            self.pagine.setCurrentWidget(self.pagina_codice)  # passa alla pagina 2

    def controlla_codice(self):
        if livello2(self.segreto, self.codice.text()):
            self.segreto = None
            self.pagine.setCurrentWidget(self.pagina_principale)  # passa alla pagina 3
        else:
            self.esito_codice.setText("Codice sbagliato")
            self.codice.clear()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    finestra = Finestra()
    finestra.show()
    sys.exit(app.exec())
