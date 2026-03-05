#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)

import os
import sys

sys.dont_write_bytecode = True
os.chdir(sys.path[0] or '.')
sys.path += ('core',)

print('########################################################')
print('#                                                      #')
print('#                  BlackJack IRC Bot                   #')
print('#           Developed by acidvegas in Python           #')
print('#             https://acid.vegas/blackjack             #')
print('#                                                      #')
print('########################################################')

if sys.version_info.major < 3:
	raise SystemExit('Python 3 is required!')
if os.name != 'nt' and os.getuid() == 0:
	raise SystemExit('Do not run as root!')

import irc
irc.BlackJack.connect()
