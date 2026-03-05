#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# config.py

class connection:
	server     = 'irc.supernets.org'
	port       = 6667
	proxy      = None
	ipv6       = False
	ssl	       = False
	ssl_verify = False
	vhost      = None
	channel	   = '#dev'
	key	       = None


class cert:
	file     = None
	key      = None
	password = None


class ident:
	nickname = 'DEALER'
	username = 'casino'
	realname = 'https://git.supernets.org/acidvegas/irc-casino'


class login:
	network  = None
	nickserv = None
	operator = None


class settings:
	cmd_char = '!'
	log      = False
	modes    = None
