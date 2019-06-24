#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# blackjack.py

import os
import sys

sys.dont_write_bytecode = True
os.chdir(sys.path[0] or '.')
sys.path += ('core',)

import debug

debug.info()
if not debug.check_version(3):
	debug.error_exit('BlackJack requires Python 3!')
elif debug.check_privileges():
	debug.error_exit('Do not run BlackJack as admin/root!')
import irc
irc.BlackJack.connect()