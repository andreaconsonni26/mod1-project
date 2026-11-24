"""Passo 3 - Il codice a 6 cifre (TOTP) con il QR code da inquadrare col telefono.

Prova:  python esempi/passo3_totp.py
(allarga la finestra del Terminale, così il QR code si vede intero)
"""
import sys

import pyotp
import qrcode

# 1. Un segreto casuale: lo conoscono solo il programma e il telefono
segreto = pyotp.random_base32()
totp = pyotp.TOTP(segreto)  # 6 cifre, nuovo codice ogni 30 secondi

# 2. L'indirizzo "otpauth://..." contiene il segreto: il QR code serve solo a trasportarlo sul telefono
uri = totp.provisioning_uri(name="andrea", issuer_name="Prova")
print(uri)
qr = qrcode.QRCode(border=2)
qr.add_data(uri)
# Disegna il QR code nel Terminale (tty=True fissa i colori, così si legge con qualsiasi tema)
qr.print_ascii(tty=sys.stdout.isatty(), invert=True)
print("Se il telefono non lo legge, inserisci a mano questa chiave:", segreto)

# 3. Il telefono calcola il codice da solo, con lo stesso segreto e lo stesso orologio
codice = input("Codice a 6 cifre mostrato dal telefono: ")
if totp.verify(codice, valid_window=1):  # valid_window=1: accetta anche il codice dei 30 s prima o dopo
    print("Codice corretto: telefono e programma sono d'accordo")
else:
    print("Codice sbagliato")
