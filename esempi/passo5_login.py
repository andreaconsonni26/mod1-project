"""Passo 5 - Login a due livelli, nel Terminale (versione didattica).

Prova:  python esempi/passo5_login.py
Al primo lancio crea l'utente e mostra il QR code; ai lanci successivi chiede i due livelli.

ATTENZIONE: per semplicità qui il segreto TOTP è salvato in chiaro. In SecureApp è cifrato
(AES-256-GCM) e ci sono anche blocco dei tentativi, anti-replay e codici di recupero.
"""
import getpass
import sqlite3
import sys

import pyotp
import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ph = PasswordHasher()
db = sqlite3.connect("prova_login.db")
db.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT, totp_secret TEXT)")


def registra(username: str, password: str) -> str:
    """Crea l'utente e restituisce l'indirizzo otpauth:// da mostrare come QR code."""
    segreto = pyotp.random_base32()
    with db:
        db.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?)", (username, ph.hash(password), segreto))
    return pyotp.TOTP(segreto).provisioning_uri(name=username, issuer_name="Prova")


def livello1(username: str, password: str) -> str | None:
    """Username + password. Se sono giusti restituisce il segreto TOTP, altrimenti None."""
    riga = db.execute("SELECT password_hash, totp_secret FROM users WHERE username = ?", (username,)).fetchone()
    if riga is None:
        return None
    try:
        ph.verify(riga[0], password)
    except VerifyMismatchError:
        return None
    return riga[1]


def livello2(segreto: str, codice: str) -> bool:
    """Codice a 6 cifre: giusto o sbagliato."""
    return pyotp.TOTP(segreto).verify(codice, valid_window=1)


if __name__ == "__main__":  # questa parte gira solo se lanci il file, non se lo importi
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        print("Nessun utente: creiamone uno.")
        uri = registra(input("Nuovo username: "), getpass.getpass("Nuova password: "))
        qr = qrcode.QRCode(border=2)
        qr.add_data(uri)
        qr.print_ascii(tty=sys.stdout.isatty(), invert=True)  # colori fissi: leggibile con qualsiasi tema
        print("Inquadra il QR code con l'app di autenticazione, poi rilancia il programma.")
    else:
        segreto = livello1(input("Username: "), getpass.getpass("Password: "))
        if segreto is None:
            print("Username o password sbagliati")
        elif livello2(segreto, input("Codice a 6 cifre: ")):
            print("Accesso consentito: entrambi i livelli superati")
        else:
            print("Codice sbagliato: accesso negato")
