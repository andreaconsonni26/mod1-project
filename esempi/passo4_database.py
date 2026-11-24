"""Passo 4 - Un piccolo database SQLite per gli utenti.

Prova:  python esempi/passo4_database.py
Crea il file prova.db nella cartella in cui lanci il comando.
"""
import sqlite3

from argon2 import PasswordHasher

ph = PasswordHasher()

db = sqlite3.connect("prova.db")  # apre il file (se non esiste lo crea)
db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username       TEXT PRIMARY KEY,
        password_hash  TEXT NOT NULL
    )
""")

# Scrittura: i valori vanno SEMPRE passati con i "?", mai incollati nel testo SQL
with db:  # "with db" = transazione: o tutto viene salvato, o niente
    db.execute("INSERT OR REPLACE INTO users (username, password_hash) VALUES (?, ?)",
               ("andrea", ph.hash("la mia password segreta")))

# Lettura
riga = db.execute("SELECT username, password_hash FROM users WHERE username = ?", ("andrea",)).fetchone()
print("Utente:", riga[0])
print("Hash salvato:", riga[1][:45], "...")
print("Login con la password giusta?", ph.verify(riga[1], "la mia password segreta"))
db.close()
