#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# debug.py

import ctypes
import os
import sys
import time

def check_privileges():
    if check_windows():
        if ctypes.windll.shell32.IsUserAnAdmin() != 0:
            return True
        else:
            return False
    else:
        if os.getuid() == 0 or os.geteuid() == 0:
            return True
        else:
            return False

def check_version(major):
    if sys.version_info.major == major:
        return True
    else:
        return False

def check_windows():
    if os.name == 'nt':
        return True
    else:
        return False

def clear():
    if check_windows():
        os.system('cls')
    else:
        os.system('clear')

def error(msg, reason=None):
    if reason:
        print(f'{get_time()} | [!] - {msg} ({reason})')
    else:
        print(f'{get_time()} | [!] - {msg}')

def error_exit(msg):
    raise SystemExit(f'{get_time()} | [!] - {msg}')

def get_time():
    return time.strftime('%I:%M:%S')

def info():
    clear()
    print(''.rjust(56, '#'))
    print('#{0}#'.format(''.center(54)))
    print('#{0}#'.format('BlackJack IRC Bot'.center(54)))
    print('#{0}#'.format('Developed by acidvegas in Python'.center(54)))
    print('#{0}#'.format('https://acid.vegas/blackjack'.center(54)))
    print('#{0}#'.format(''.center(54)))
    print(''.rjust(56, '#'))

def irc(msg):
    print(f'{get_time()} | [~] - {msg}')