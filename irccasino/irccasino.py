#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)

import os
import sys

sys.dont_write_bytecode = True
os.chdir(sys.path[0] or '.')
sys.path += ('core',)

import irc

print('########################################################')
print('#                                                      #')
print('#                  BlackJack IRC Bot                   #')
print('#           Developed by acidvegas in Python           #')
print('#             https://acid.vegas/blackjack             #')
print('#                                                      #')
print('########################################################')

irc.BlackJack.connect()
