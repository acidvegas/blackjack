#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# config.py

class connection:
	server  = 'irc.supernets.org'
	port    = 6697
	ssl	    = True
	channel	= '#superbowl'


class ident:
	nickname = 'DEALER'
	username = 'casino'
	realname = 'https://git.supernets.org/acidvegas/irc-casino'
	nickserv = None


class settings:
	cmd_char = '!'
	log      = False
	modes    = 'BdDg'
