"""Passo 2 - Salvare una password nel modo giusto: l'hash Argon2id.

Prova:  python esempi/passo2_hash.py
"""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ph = PasswordHasher()  # l'oggetto che sa calcolare e verificare gli hash Argon2id

# 1. Alla registrazione: calcolo l'hash e salvo SOLO quello (mai la password)
impronta = ph.hash("la mia password segreta")
print("Questo è ciò che salverei nel database:")
print(" ", impronta)

# 2. Al login: ricalcolo dalla password digitata e confronto
print("Password giusta accettata?", ph.verify(impronta, "la mia password segreta"))

try:
    ph.verify(impronta, "una password sbagliata")
except VerifyMismatchError:
    print("Password sbagliata: rifiutata")

# 3. La stessa password dà ogni volta un hash diverso, perché cambia il "salt" casuale
print("Stessa password, secondo hash:")
print(" ", ph.hash("la mia password segreta"))
