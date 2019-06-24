#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# functions.py

import datetime
import os
import sqlite3

# Globals
db  = sqlite3.connect(os.path.join('data', 'bot.db'), check_same_thread=False)
sql = db.cursor()

def check():
	tables = sql.execute('SELECT name FROM sqlite_master WHERE type=\'table\'').fetchall()
	if not len(tables):
		sql.execute('CREATE TABLE IGNORE (IDENT TEXT NOT NULL);')
		db.commit()

class Player:
    def register(nick, ident):
        now = str(datetime.datetime.now())
        sql.execute('INSERT INTO PLAYERS (NICK,IDENT,MONEY,LAST) VALUES (\'{0}\', \'{1}\', \'{2}\', \'{3}\')'.format(nick, ident, '0', now))
        db.commit()

    def get_money(ident):
        return sql.execute('SELECT MONEY FROM PLAYERS WHERE IDENT=\'{0}\''.format(ident)).fetchall()[0][0]

