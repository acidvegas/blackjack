#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# irc.py

import json
import os
import re
import socket
import ssl
import threading
import time
import traceback

import config

from cards import (
	NUM_DECKS, MAX_PLAYERS, DEFAULT_BET, MIN_BET, MAX_BET, STARTING_CHIPS,
	MOVE_TIMEOUT, LOBBY_TIMEOUT, DB_SYNC_INTERVAL, RESET_COOLDOWN,
	SMALL_BLIND, BIG_BLIND, HOUSE_STARTING,
	SUITS, CARD_ART, FACEDOWN,
	hand_value, poker_best_hand, poker_hand_name, poker_calculate_pots,
	Shoe, Player, PokerPlayer,
)

# --- IRC Formatting ---
bold  = '\x02'
reset = '\x0f'

white      = '00'
black      = '01'
blue       = '02'
green      = '03'
red        = '04'
orange     = '07'
yellow     = '08'
cyan       = '11'
light_blue = '12'
pink       = '13'
grey       = '14'

sym_arrow = '\u2192'
sym_check = '\u2713'
sym_cross = '\u2717'
sym_dash  = '\u2500'
sym_star  = '\u2605'
sep       = f'\x03{grey}|\x0f'

BJ_HEADER    = " \u2660 \u2764  BLACKJACK  \u2666 \u2663 "
POKER_HEADER = " \u2660 \u2764  TEXAS HOLD'EM  \u2666 \u2663 "


def color(msg, foreground, background=None):
	if background:
		return f'\x03{foreground},{background}{msg}{reset}'
	return f'\x03{foreground}{msg}{reset}'


def c_money(val):
	if isinstance(val, (int, float)):
		return color(f'${val:,}', green)
	return color(f'${val}', green)


def c_loss(val):
	if isinstance(val, (int, float)):
		return color(f'-${abs(val):,}', red)
	return color(f'-${val}', red)


def c_nick(name):
	return color(str(name), cyan)


def c_cmd(command):
	return color(str(command), pink)


def c_arg(text):
	return color(str(text), grey)


_irc_strip_re = re.compile(r'\x03(\d{1,2}(,\d{1,2})?)?|\x02|\x0f|\x16|\x1d|\x1f')

def _vis_len(s):
	return len(_irc_strip_re.sub('', s))

def _pad(s, width):
	return s + ' ' * max(0, width - _vis_len(s))

def _log(msg):
	print(f'{time.strftime("%H:%M:%S")} | {msg}')


# --- Card Display (IRC-formatted) ---

def format_card(rank, suit):
	sym, is_red = SUITS[suit]
	return color(f'{rank}{sym}', red if is_red else black, white)


def format_hand(cards, hide_first=False):
	if hide_first and len(cards) > 1:
		return color('[??]', grey, white) + ' ' + ' '.join(format_card(r, s) for r, s in cards[1:])
	return ' '.join(format_card(r, s) for r, s in cards)


def render_hand(cards, hide_first=False):
	lines = [[] for _ in range(5)]
	for i, (rank, suit) in enumerate(cards):
		if hide_first and i == 0:
			for j in range(5):
				lines[j].append(color(FACEDOWN[j], light_blue, blue))
		else:
			sym, is_red = SUITS[suit]
			card_color = red if is_red else black
			art = CARD_ART[rank]
			for j in range(5):
				lines[j].append(color(art[j].replace('X', sym), card_color, white))
	return [' '.join(line) for line in lines]


# --- IRC Bot ---

class IRC:
	def __init__(self):
		self.sock    = None
		self.db      = {}
		self.db_path = os.path.join('data', 'chips.json')
		self.shoe = Shoe(NUM_DECKS)
		self.lock = threading.Lock()
		self.chan = None

		# Display
		self.mini_mode = False

		# Blackjack state
		self.state       = 'idle'
		self.players     = []
		self.dealer_hand = []
		self.current_idx = 0
		self.last_move   = 0
		self.lobby_timer = None

		# Poker state
		self.pk_state       = 'idle'
		self.pk_street      = None
		self.pk_players     = []
		self.pk_community   = []
		self.pk_current_idx = 0
		self.pk_dealer_btn  = 0
		self.pk_current_bet = 0
		self.pk_min_raise   = BIG_BLIND
		self.pk_last_move   = 0
		self.pk_lobby_timer = None
		self.pk_cards_shown = False

	# ──────────────────── IRC Protocol ────────────────────

	def connect(self):
		try:
			self.create_socket()
			self.sock.connect((config.connection.server, config.connection.port))
			if config.login.network:
				self.raw('PASS ' + config.login.network)
			self.raw(f'USER {config.ident.username} 0 * :{config.ident.realname}')
			self.raw('NICK ' + config.ident.nickname)
		except socket.error as ex:
			_log(f'[!] Failed to connect to IRC server: {ex}')
			self.event_disconnect()
		else:
			self.listen()

	def create_socket(self):
		family    = socket.AF_INET6 if config.connection.ipv6 else socket.AF_INET
		self.sock = socket.socket(family, socket.SOCK_STREAM)
		if config.connection.vhost:
			self.sock.bind((config.connection.vhost, 0))
		if config.connection.ssl:
			ctx = ssl.create_default_context()
			if not config.connection.ssl_verify:
				ctx.check_hostname = False
				ctx.verify_mode    = ssl.CERT_NONE
			if config.cert.file:
				ctx.load_cert_chain(config.cert.file, config.cert.key, config.cert.password)
			self.sock = ctx.wrap_socket(self.sock, server_hostname=config.connection.server)

	def raw(self, msg):
		_log(f'[>>] {msg}')
		self.sock.send(bytes(msg + '\r\n', 'utf-8'))

	def sendmsg(self, target, msg):
		self.raw(f'PRIVMSG {target} :{msg}')

	def blank(self, chan):
		self.raw(f'PRIVMSG {chan} :\x0f')

	def show_cards(self, target, label, cards, hide_first=False):
		is_channel = target[0] in '#&'
		if is_channel and self.mini_mode:
			compact = format_hand(cards, hide_first)
			self.sendmsg(target, f'{label} {compact}')
		else:
			lines = render_hand(cards, hide_first)
			self.sendmsg(target, label)
			for line in lines:
				self.raw(f'PRIVMSG {target} :{line}')

	def join(self, chan, key=None):
		self.raw(f'JOIN {chan} {key}') if key else self.raw(f'JOIN {chan}')

	def identify(self, username, password):
		self.sendmsg('NickServ', f'IDENTIFY {username} {password}')

	def listen(self):
		buf = b''
		while True:
			try:
				chunk = self.sock.recv(4096)
				if not chunk:
					_log('[!] No data received from server.')
					break
				buf += chunk
				while b'\r\n' in buf:
					line_bytes, buf = buf.split(b'\r\n', 1)
					try:
						line = line_bytes.decode('utf-8')
					except UnicodeDecodeError:
						line = line_bytes.decode('latin-1')
					if not line or len(line.split()) < 2:
						continue
					_log(f'[<<] {line}')
					if line.startswith('ERROR :Closing Link:'):
						raise Exception('Connection has closed.')
					self.handle_events(line)
			except Exception as ex:
				_log(f'[!] Unexpected error: {ex}')
				traceback.print_exc()
				break
		self.event_disconnect()

	def handle_events(self, data):
		args = data.split()
		if args[0] == 'PING':
			self.raw('PONG ' + args[1][1:])
		elif args[1] == '001':
			self.event_connect()
		elif args[1] == '433':
			raise SystemExit('Nickname is already in use.')
		elif args[1] in ('KICK', 'PART', 'PRIVMSG', 'QUIT'):
			nick = args[0].split('!')[0][1:]
			if nick != config.ident.nickname:
				if args[1] == 'KICK':
					self.event_kick(nick, args[2], args[3])
				elif args[1] == 'PART':
					self.event_part(nick, args[2])
				elif args[1] == 'PRIVMSG':
					chan = args[2]
					msg  = data.split(f'{args[0]} PRIVMSG {chan} :')[1]
					if chan != config.ident.nickname:
						self.event_message(nick, chan, msg)
				elif args[1] == 'QUIT':
					self.event_quit(nick)

	# ──────────────────── Events ────────────────────

	def event_connect(self):
		try:
			self.load_db()
		except Exception as ex:
			_log(f'[!] Failed to load database: {ex}')
			traceback.print_exc()
		if config.login.nickserv:
			self.identify(config.ident.username, config.login.nickserv)
		if config.login.operator:
			self.raw(f'OPER {config.ident.username} {config.login.operator}')
		self.join(config.connection.channel, config.connection.key)
		self.chan = config.connection.channel

	def event_disconnect(self):
		if self.db is not None:
			self.save_db()
		self.sock.close()
		self.reset_game()
		self._pk_reset()
		time.sleep(10)
		self.connect()

	def event_kick(self, nick, chan, kicked):
		if kicked == config.ident.nickname and chan.lower() == config.connection.channel.lower():
			time.sleep(3)
			self.join(config.connection.channel, config.connection.key)
		else:
			self.player_left(kicked, chan)

	def event_part(self, nick, chan):
		if chan.lower() == config.connection.channel.lower():
			self.player_left(nick, chan)

	def event_quit(self, nick):
		self.player_left(nick, config.connection.channel)

	def event_message(self, nick, chan, msg):
		if chan.lower() != config.connection.channel.lower():
			return
		parts = msg.split()
		if not parts:
			return
		cmd = parts[0].lower()

		try:
			if   cmd in ('!blackjack', '!bj'):   self.cmd_blackjack(nick, chan, parts[1:])
			elif cmd == '!hit':                   self.cmd_hit(nick, chan)
			elif cmd == '!stand':                 self.cmd_stand(nick, chan)
			elif cmd in ('!double', '!dd'):       self.cmd_double(nick, chan)
			elif cmd in ('!poker', '!pk'):        self.cmd_poker(nick, chan)
			elif cmd == '!fold':                  self.cmd_fold(nick, chan)
			elif cmd == '!check':                 self.cmd_check(nick, chan)
			elif cmd == '!call':                  self.cmd_call(nick, chan)
			elif cmd in ('!raise', '!bet'):       self.cmd_raise(nick, chan, parts[1:])
			elif cmd == '!allin':                 self.cmd_allin(nick, chan)
			elif cmd == '!deal':
				if self.state == 'lobby':         self.cmd_deal(nick, chan)
				elif self.pk_state == 'lobby':    self.cmd_pk_deal(nick, chan)
			elif cmd == '!leave':                 self.cmd_leave(nick, chan)
			elif cmd == '!chips':                 self.cmd_chips(nick, chan, parts[1:])
			elif cmd == '!top':                   self.cmd_top(nick, chan)
			elif cmd == '@casino':                self.cmd_help(nick, chan)
			elif cmd == '!mini':                  self.cmd_mini(nick, chan)
			elif cmd == '!cheat':                 self.cmd_cheat(nick, chan)
		except Exception as ex:
			_log(f'[!] Command error ({cmd}): {ex}')
			traceback.print_exc()
			try:
				self.sendmsg(chan, f'{color("ERROR", red)} {ex}')
			except Exception:
				pass

	# ──────────────────── Helpers ────────────────────

	def _game_busy(self, chan, allowed=None):
		if self.state != 'idle' and allowed != 'blackjack':
			names = ', '.join(c_nick(p.nick) for p in self.players)
			status = 'lobby' if self.state == 'lobby' else 'round'
			self.sendmsg(chan, f'{color("ERROR", red)} Blackjack {status} in progress with {names}. Wait for it to finish.')
			return True
		if self.pk_state != 'idle' and allowed != 'poker':
			names = ', '.join(c_nick(p.nick) for p in self.pk_players)
			status = 'lobby' if self.pk_state == 'lobby' else 'hand'
			self.sendmsg(chan, f'{color("ERROR", red)} Poker {status} in progress with {names}. Wait for it to finish.')
			return True
		return False

	# ──────────────────── Database ────────────────────

	def load_db(self):
		self.db_path = os.path.join('data', 'chips.json')
		os.makedirs('data', exist_ok=True)
		if os.path.exists(self.db_path):
			try:
				with open(self.db_path, 'r') as f:
					self.db = json.load(f)
				_log(f'Chip database loaded ({len(self.db)} players).')
			except Exception as ex:
				_log(f'[!] Database load failed ({ex}), starting fresh.')
				traceback.print_exc()
				self.db = {}
		else:
			self.db = {}
			_log('No database found, starting fresh.')
		if '_house' not in self.db:
			self.db['_house'] = {'chips': HOUSE_STARTING}
		if '_resets' not in self.db:
			self.db['_resets'] = {}
		self.save_db()
		threading.Thread(target=self._db_sync_loop, daemon=True).start()
		threading.Thread(target=self._reset_check_loop, daemon=True).start()

	def save_db(self):
		if self.db is None:
			return
		try:
			db_path = getattr(self, 'db_path', os.path.join('data', 'chips.json'))
			os.makedirs('data', exist_ok=True)
			with open(db_path, 'w') as f:
				json.dump(self.db, f, indent=2)
		except Exception as ex:
			_log(f'[!] Database save failed: {ex}')
			traceback.print_exc()

	def _db_sync_loop(self):
		while True:
			time.sleep(DB_SYNC_INTERVAL)
			self.save_db()

	def _reset_check_loop(self):
		while True:
			time.sleep(60)
			try:
				resets = self.db.get('_resets', {})
				done = []
				for nick_lower, req_time in list(resets.items()):
					if time.time() - req_time >= RESET_COOLDOWN:
						data = self.get_player_data(nick_lower)
						data['chips'] = STARTING_CHIPS
						data['last_reset'] = time.time()
						done.append(nick_lower)
						if self.chan:
							self.sendmsg(self.chan, f'{c_nick(nick_lower)} has received their {c_money(STARTING_CHIPS)} chip reset!')
				for nick_lower in done:
					del resets[nick_lower]
				if done:
					self.save_db()
			except Exception as ex:
				_log(f'[!] Reset check error: {ex}')
				traceback.print_exc()

	def house_chips(self):
		return self.db.get('_house', {}).get('chips', HOUSE_STARTING)

	def house_add(self, amount):
		if '_house' not in self.db:
			self.db['_house'] = {'chips': HOUSE_STARTING}
		self.db['_house']['chips'] += amount

	def get_player_data(self, nick):
		if self.db is None:
			self.db = {}
		key = nick.lower()
		if key not in self.db:
			self.db[key] = {'chips': STARTING_CHIPS, 'last_reset': 0}
		return self.db[key]

	def get_chips(self, nick):
		return self.get_player_data(nick)['chips']

	def add_chips(self, nick, amount):
		data = self.get_player_data(nick)
		data['chips'] = max(0, data['chips'] + amount)
		if data['chips'] == 0:
			data['last_reset'] = time.time()
		return data['chips']

	# ──────────────────── Blackjack Commands ────────────────────

	def cmd_blackjack(self, nick, chan, args):
		with self.lock:
			if self._game_busy(chan, allowed='blackjack'):
				return

			bet = DEFAULT_BET
			if args:
				try:
					bet = int(args[0])
				except ValueError:
					self.sendmsg(chan, f'{color("ERROR", red)} Invalid bet amount.')
					return
				if bet < MIN_BET:
					self.sendmsg(chan, f'{color("ERROR", red)} Minimum bet is {c_money(MIN_BET)}.')
					return
				if bet > MAX_BET:
					self.sendmsg(chan, f'{color("ERROR", red)} Maximum bet is {c_money(MAX_BET)}.')
					return

			chips = self.get_chips(nick)
			if chips < bet:
				self.sendmsg(chan, f'{color("ERROR", red)} {c_nick(nick)}: you have {c_money(chips)}. Use {c_cmd("!chips")} to reset if broke.')
				return

			if self.state == 'idle':
				self.state   = 'lobby'
				self.players = [Player(nick, bet)]
				self.dealer_hand = []
				self.current_idx = 0
				self.sendmsg(chan, f'{bold}{color(BJ_HEADER, black, green)}{bold} {c_nick(nick)} opened a table! Type {c_cmd("!blackjack")} {c_arg("[bet]")} to join or {c_cmd("!deal")} to start. Bets {c_money(bet)} \u2014 {LOBBY_TIMEOUT}s until auto-deal')
				self.lobby_timer = threading.Timer(LOBBY_TIMEOUT, self._lobby_expired, [chan])
				self.lobby_timer.daemon = True
				self.lobby_timer.start()

			elif self.state == 'lobby':
				if any(p.nick.lower() == nick.lower() for p in self.players):
					self.sendmsg(chan, f'{c_nick(nick)}: you are already at the table.')
					return
				if len(self.players) >= MAX_PLAYERS:
					self.sendmsg(chan, f'{color("ERROR", red)} Table is full ({MAX_PLAYERS} players).')
					return
				self.players.append(Player(nick, bet))
				self.sendmsg(chan, f'{c_nick(nick)} joins the table! ({c_money(bet)} bet) [{len(self.players)}/{MAX_PLAYERS}]')
				if len(self.players) >= MAX_PLAYERS:
					if self.lobby_timer:
						self.lobby_timer.cancel()
					self._start_round(chan)

			elif self.state == 'playing':
				names = ', '.join(c_nick(p.nick) for p in self.players)
				self.sendmsg(chan, f'{color("ERROR", red)} Round in progress with {names}. Wait for it to finish.')

	def cmd_deal(self, nick, chan):
		with self.lock:
			if self.state != 'lobby':
				return
			if nick.lower() != self.players[0].nick.lower():
				self.sendmsg(chan, f'{color("ERROR", red)} Only {c_nick(self.players[0].nick)} can start the deal.')
				return
			if self.lobby_timer:
				self.lobby_timer.cancel()
			self._start_round(chan)

	def cmd_hit(self, nick, chan):
		with self.lock:
			if self.state != 'playing':
				return
			if self.current_idx >= len(self.players):
				return
			current = self.players[self.current_idx]
			if nick.lower() != current.nick.lower():
				if any(p.nick.lower() == nick.lower() for p in self.players):
					self.sendmsg(chan, f"{c_nick(nick)}: it's {c_nick(current.nick)}'s turn.")
				return

			card = self.shoe.draw()
			current.hand.append(card)
			self.last_move = time.time()

			if current.total > 21:
				current.status = 'busted'
				self.show_cards(chan, f'{bold}[{c_nick(current.nick)}]{bold} ({color(str(current.total), light_blue)}) \u2014 {color("BUST!", red)}', current.hand)
				self._next_player(chan)
			elif current.total == 21:
				current.status = 'stood'
				self.show_cards(chan, f'{bold}[{c_nick(current.nick)}]{bold} ({color(str(current.total), light_blue)}) \u2014 {color("21!", green)}', current.hand)
				self._next_player(chan)
			else:
				self.show_cards(chan, f'{bold}[{c_nick(current.nick)}]{bold} ({color(str(current.total), light_blue)})', current.hand)

	def cmd_stand(self, nick, chan):
		with self.lock:
			if self.state != 'playing':
				return
			if self.current_idx >= len(self.players):
				return
			current = self.players[self.current_idx]
			if nick.lower() != current.nick.lower():
				return
			current.status = 'stood'
			self.last_move = time.time()
			self.sendmsg(chan, f'{c_nick(current.nick)} stands at {bold}{current.total}{bold}.')
			self._next_player(chan)

	def cmd_double(self, nick, chan):
		with self.lock:
			if self.state != 'playing':
				return
			if self.current_idx >= len(self.players):
				return
			current = self.players[self.current_idx]
			if nick.lower() != current.nick.lower():
				return
			if len(current.hand) != 2:
				self.sendmsg(chan, f'{color("ERROR", red)} You can only double down on your first two cards.')
				return
			chips = self.get_chips(nick)
			if chips < current.bet * 2:
				self.sendmsg(chan, f'{color("ERROR", red)} Need {c_money(current.bet * 2)} to double down (you have {c_money(chips)}).')
				return

			current.bet *= 2
			card = self.shoe.draw()
			current.hand.append(card)
			self.last_move = time.time()
			self.sendmsg(chan, f'{c_nick(current.nick)} doubles down! Bet is now {c_money(current.bet)}')

			if current.total > 21:
				current.status = 'busted'
				self.show_cards(chan, f'{bold}[{c_nick(current.nick)}]{bold} ({color(str(current.total), light_blue)}) \u2014 {color("BUST!", red)}', current.hand)
			else:
				current.status = 'stood'
				self.show_cards(chan, f'{bold}[{c_nick(current.nick)}]{bold} ({color(str(current.total), light_blue)})', current.hand)
			self._next_player(chan)

	# ──────────────────── Poker Commands ────────────────────

	def cmd_poker(self, nick, chan):
		with self.lock:
			if self._game_busy(chan, allowed='poker'):
				return

			if self.pk_state == 'idle':
				chips = self.get_chips(nick)
				if chips < BIG_BLIND:
					self.sendmsg(chan, f'{color("ERROR", red)} {c_nick(nick)}: need at least {c_money(BIG_BLIND)} to play. Use {c_cmd("!chips")}.')
					return
				self.pk_state   = 'lobby'
				self.pk_players = [PokerPlayer(nick)]
				self.pk_community   = []
				self.pk_current_bet = 0
				self.pk_cards_shown = False
				self.sendmsg(chan, f'{bold}{color(POKER_HEADER, black, green)}{bold} {c_nick(nick)} opened a poker table! Type {c_cmd("!poker")} to join or {c_cmd("!deal")} to start. Blinds: {c_money(SMALL_BLIND)}/{c_money(BIG_BLIND)} \u2014 {LOBBY_TIMEOUT}s until auto-deal')
				self.pk_lobby_timer = threading.Timer(LOBBY_TIMEOUT, self._pk_lobby_expired, [chan])
				self.pk_lobby_timer.daemon = True
				self.pk_lobby_timer.start()

			elif self.pk_state == 'lobby':
				if any(p.nick.lower() == nick.lower() for p in self.pk_players):
					self.sendmsg(chan, f'{c_nick(nick)}: you are already at the table.')
					return
				if len(self.pk_players) >= MAX_PLAYERS:
					self.sendmsg(chan, f'{color("ERROR", red)} Table is full ({MAX_PLAYERS} players).')
					return
				chips = self.get_chips(nick)
				if chips < BIG_BLIND:
					self.sendmsg(chan, f'{color("ERROR", red)} {c_nick(nick)}: need at least {c_money(BIG_BLIND)} to play.')
					return
				self.pk_players.append(PokerPlayer(nick))
				self.sendmsg(chan, f'{c_nick(nick)} joins the table! [{len(self.pk_players)}/{MAX_PLAYERS}]')
				if len(self.pk_players) >= MAX_PLAYERS:
					if self.pk_lobby_timer:
						self.pk_lobby_timer.cancel()
					self._pk_start_hand(chan)

			elif self.pk_state == 'playing':
				names = ', '.join(c_nick(p.nick) for p in self.pk_players)
				self.sendmsg(chan, f'{color("ERROR", red)} Poker hand in progress with {names}. Wait for it to finish.')

	def cmd_pk_deal(self, nick, chan):
		with self.lock:
			if self.pk_state != 'lobby':
				return
			if len(self.pk_players) < 2:
				self.sendmsg(chan, f'{color("ERROR", red)} Need at least 2 players to start.')
				return
			if nick.lower() != self.pk_players[0].nick.lower():
				self.sendmsg(chan, f'{color("ERROR", red)} Only {c_nick(self.pk_players[0].nick)} can start the deal.')
				return
			if self.pk_lobby_timer:
				self.pk_lobby_timer.cancel()
			self._pk_start_hand(chan)

	def cmd_fold(self, nick, chan):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return
			current.folded = True
			current.acted  = True
			self._pk_after_action(chan, f'{c_nick(current.nick)} folds.')

	def cmd_check(self, nick, chan):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return
			if current.bet < self.pk_current_bet:
				to_call = self.pk_current_bet - current.bet
				self.sendmsg(chan, f'{color("ERROR", red)} Cannot check \u2014 {c_money(to_call)} to call. Use {c_cmd("!call")}, {c_cmd("!raise")}, or {c_cmd("!fold")}.')
				return
			current.acted = True
			self._pk_after_action(chan, f'{c_nick(current.nick)} checks.')

	def cmd_call(self, nick, chan):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return
			to_call = self.pk_current_bet - current.bet
			if to_call <= 0:
				self.sendmsg(chan, f'{color("ERROR", red)} Nothing to call. Use {c_cmd("!check")}.')
				return

			available = self.get_chips(current.nick) - current.total_bet
			if to_call >= available:
				to_call = available
				current.all_in = True

			current.total_bet += to_call
			current.bet       += to_call
			current.acted      = True

			if current.all_in:
				msg = f'{c_nick(current.nick)} calls all-in {c_money(to_call)}'
			else:
				msg = f'{c_nick(current.nick)} calls {c_money(to_call)}'
			self._pk_after_action(chan, msg)

	def cmd_raise(self, nick, chan, args):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return
			if not args:
				self.sendmsg(chan, f'{color("ERROR", red)} Usage: {c_cmd("!raise")} {c_arg("<total>")} (e.g. !raise {self.pk_current_bet + self.pk_min_raise})')
				return
			try:
				raise_to = int(args[0])
			except ValueError:
				self.sendmsg(chan, f'{color("ERROR", red)} Invalid amount.')
				return

			min_total = self.pk_current_bet + self.pk_min_raise
			if raise_to < min_total:
				self.sendmsg(chan, f'{color("ERROR", red)} Minimum raise is to {c_money(min_total)}.')
				return

			cost = raise_to - current.bet
			available = self.get_chips(current.nick) - current.total_bet
			if cost > available:
				self.sendmsg(chan, f'{color("ERROR", red)} Not enough chips. You can bet up to {c_money(current.bet + available)}. Use {c_cmd("!allin")}.')
				return

			raise_diff = raise_to - self.pk_current_bet
			self.pk_min_raise   = raise_diff
			self.pk_current_bet = raise_to
			current.total_bet  += cost
			current.bet         = raise_to
			current.acted       = True

			for p in self.pk_players:
				if p is not current and not p.folded and not p.all_in:
					p.acted = False

			self._pk_after_action(chan, f'{c_nick(current.nick)} raises to {c_money(raise_to)}')

	def cmd_allin(self, nick, chan):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return

			available = self.get_chips(current.nick) - current.total_bet
			if available <= 0:
				self.sendmsg(chan, f'{color("ERROR", red)} You have no chips to bet.')
				return

			allin_total = current.bet + available
			current.total_bet += available
			current.bet        = allin_total
			current.all_in     = True
			current.acted      = True

			if allin_total >= self.pk_current_bet + self.pk_min_raise:
				self.pk_min_raise   = allin_total - self.pk_current_bet
				self.pk_current_bet = allin_total
				for p in self.pk_players:
					if p is not current and not p.folded and not p.all_in:
						p.acted = False
			elif allin_total > self.pk_current_bet:
				self.pk_current_bet = allin_total

			self._pk_after_action(chan, f'{c_nick(current.nick)} goes ALL-IN for {c_money(available)}!')

	# ──────────────────── Shared Commands ────────────────────

	def cmd_leave(self, nick, chan):
		with self.lock:
			if self.state == 'lobby':
				idx = next((i for i, p in enumerate(self.players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.players.pop(idx)
					self.sendmsg(chan, f'{c_nick(nick)} left the table. [{len(self.players)}/{MAX_PLAYERS}]')
					if not self.players:
						if self.lobby_timer:
							self.lobby_timer.cancel()
						self.state = 'idle'
						self.sendmsg(chan, 'Table closed \u2014 no players remain.')

			elif self.pk_state == 'lobby':
				idx = next((i for i, p in enumerate(self.pk_players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.pk_players.pop(idx)
					self.sendmsg(chan, f'{c_nick(nick)} left the poker table. [{len(self.pk_players)}/{MAX_PLAYERS}]')
					if not self.pk_players:
						if self.pk_lobby_timer:
							self.pk_lobby_timer.cancel()
						self._pk_reset()
						self.sendmsg(chan, 'Poker table closed.')

	def cmd_chips(self, nick, chan, args=None):
		data  = self.get_player_data(nick)
		chips = data['chips']
		resets = self.db.setdefault('_resets', {})
		nick_lower = nick.lower()

		if args and args[0].lower() == 'reset':
			if nick_lower in resets:
				remaining = RESET_COOLDOWN - (time.time() - resets[nick_lower])
				if remaining > 0:
					hours   = int(remaining // 3600)
					minutes = int((remaining % 3600) // 60)
					self.sendmsg(chan, f'{c_nick(nick)}: reset pending. Chips arrive in {bold}{hours}h {minutes}m{bold}.')
				else:
					self.sendmsg(chan, f'{c_nick(nick)}: reset processing momentarily.')
				return
			last_reset = data.get('last_reset', 0)
			elapsed = time.time() - last_reset if last_reset > 0 else float('inf')
			if elapsed < RESET_COOLDOWN:
				remaining = RESET_COOLDOWN - elapsed
				hours     = int(remaining // 3600)
				minutes   = int((remaining % 3600) // 60)
				self.sendmsg(chan, f'{c_nick(nick)}: reset on cooldown. Try again in {bold}{hours}h {minutes}m{bold}.')
				return
			resets[nick_lower] = time.time()
			self.save_db()
			self.sendmsg(chan, f'{c_nick(nick)} has requested a chip reset. {c_money(STARTING_CHIPS)} will be granted in {bold}24 hours{bold}.')
			return

		if nick_lower in resets:
			remaining = RESET_COOLDOWN - (time.time() - resets[nick_lower])
			if remaining > 0:
				hours   = int(remaining // 3600)
				minutes = int((remaining % 3600) // 60)
				self.sendmsg(chan, f'{c_nick(nick)}: {c_money(chips)} chips. Reset pending in {bold}{hours}h {minutes}m{bold}.')
			else:
				self.sendmsg(chan, f'{c_nick(nick)}: reset processing momentarily.')
			return

		if chips <= 0:
			self.sendmsg(chan, f'{c_nick(nick)}: broke. Use {c_cmd("!chips")} {c_arg("reset")} to request a new {c_money(STARTING_CHIPS)} bankroll (24h wait).')
		else:
			self.sendmsg(chan, f'{c_nick(nick)} has {c_money(chips)} in chips. Use {c_cmd("!chips")} {c_arg("reset")} to request a reset to {c_money(STARTING_CHIPS)} (24h wait).')

	def cmd_top(self, nick, chan):
		if self.db is None:
			self.sendmsg(chan, f'{color("ERROR", red)} Database not loaded.')
			return
		if not self.db:
			self.sendmsg(chan, 'No players registered yet.')
			return
		leaderboard = []
		for key, data in self.db.items():
			if key.startswith('_'):
				continue
			if isinstance(data, dict) and 'chips' in data:
				leaderboard.append((key, data['chips']))
		leaderboard.sort(key=lambda x: x[1], reverse=True)
		house = self.house_chips()
		self.sendmsg(chan, f'{bold}{color(" TOP 10 ", black, green)}{bold}')
		self.sendmsg(chan, f' House: {c_money(house)}')
		for i, (name, chips) in enumerate(leaderboard[:10], 1):
			pad = name[:20].ljust(20)
			self.sendmsg(chan, f' {bold}#{i:<2}{bold} {c_nick(pad)} {c_money(chips)}')

	def cmd_help(self, nick, chan):
		url = 'https://github.com/acidvegas/irc-casino'
		self.sendmsg(chan, f'{bold}{color(" IRC CASINO ", black, green)}{bold} {sep} {color(url, light_blue)}')
		cmds = [
			(c_cmd('!blackjack') + ' ' + c_arg('[bet]'), f'Start or join a blackjack table (default: {c_money(DEFAULT_BET)})'),
			(c_cmd('!hit'),                               'Draw a card'),
			(c_cmd('!stand'),                             'Keep your hand'),
			(c_cmd('!double'),                            'Double down'),
			(c_cmd('!poker'),                             f'Start or join a poker table (blinds {c_money(SMALL_BLIND)}/{c_money(BIG_BLIND)})'),
			(c_cmd('!check'),                             'Check (poker)'),
			(c_cmd('!call'),                              'Call current bet (poker)'),
			(c_cmd('!raise') + ' ' + c_arg('<amt>'),      'Raise to amount (poker)'),
			(c_cmd('!fold'),                              'Fold hand (poker)'),
			(c_cmd('!allin'),                             'Go all-in (poker)'),
			(c_cmd('!deal'),                              'Force deal if lobby open'),
			(c_cmd('!leave'),                             'Leave the lobby'),
			(c_cmd('!chips'),                             'Check your chip balance'),
			(c_cmd('!chips') + ' ' + c_arg('reset'),      'Request chip reset (24h wait)'),
			(c_cmd('!top'),                               'Leaderboard'),
			(c_cmd('!mini'),                              'Toggle compact card display'),
			(c_cmd('!cheat'),                             'Blackjack strategy sheet'),
		]
		for cmd, desc in cmds:
			self.sendmsg(chan, f' {_pad(cmd, 20)} {sep} {desc}')

	def cmd_mini(self, nick, chan):
		self.mini_mode = not self.mini_mode
		mode = 'compact' if self.mini_mode else 'full-size'
		self.sendmsg(chan, f'Card display set to {bold}{mode}{bold}.')

	def cmd_cheat(self, nick, chan):
		cheat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'cheat.txt')
		try:
			with open(cheat_path, 'r') as f:
				for line in f:
					line = line.rstrip('\n')
					if line:
						self.sendmsg(chan, line)
					else:
						self.raw(f'PRIVMSG {chan} :\x0f')
		except FileNotFoundError:
			self.sendmsg(chan, f'{color("ERROR", red)} cheat.txt not found.')

	# ──────────────────── Blackjack Logic ────────────────────

	def _lobby_expired(self, chan):
		with self.lock:
			if self.state == 'lobby':
				self._start_round(chan)

	def _check_shuffle(self, chan):
		if self.shoe.needs_shuffle or self.shoe.past_cut:
			self.shoe.shuffle()
			self.sendmsg(chan, f'{bold}{color(" SHUFFLING ALL CARDS ", black, yellow)}{bold}')

	def _start_round(self, chan):
		self._check_shuffle(chan)
		self.state       = 'playing'
		self.current_idx = 0
		self.dealer_hand = []

		for _ in range(2):
			for player in self.players:
				player.hand.append(self.shoe.draw())
			self.dealer_hand.append(self.shoe.draw())

		self.sendmsg(chan, f'{bold}{color(" CARDS DEALT ", black, orange)}{bold}')
		self.show_cards(chan, f'{bold}[Dealer]{bold}', self.dealer_hand, hide_first=True)
		self.blank(chan)

		for player in self.players:
			bj = ''
			if player.is_blackjack:
				player.status = 'blackjack'
				bj = f' {color("BLACKJACK!", green)}'
			self.show_cards(chan, f'{bold}[{c_nick(player.nick)}]{bold} ({color(str(player.total), light_blue)}){bj}', player.hand)
			self.blank(chan)

		self.last_move = time.time()
		threading.Thread(target=self._move_timer, args=(chan,), daemon=True).start()
		self._advance(chan)

	def _advance(self, chan):
		while self.current_idx < len(self.players):
			if self.players[self.current_idx].status == 'playing':
				p = self.players[self.current_idx]
				self.last_move = time.time()
				if len(p.hand) == 2:
					self.sendmsg(chan, f"{c_nick(p.nick)}: your turn \u2014 {c_cmd('!hit')}, {c_cmd('!stand')}, or {c_cmd('!double')}")
				else:
					self.sendmsg(chan, f"{c_nick(p.nick)}: your turn \u2014 {c_cmd('!hit')} or {c_cmd('!stand')}")
				return
			self.current_idx += 1
		self._dealer_turn(chan)

	def _next_player(self, chan):
		self.current_idx += 1
		self._advance(chan)

	def _dealer_turn(self, chan):
		all_busted = all(p.status == 'busted' for p in self.players)

		self.sendmsg(chan, f'{bold}{color(" DEALER REVEALS ", black, orange)}{bold}')
		dtotal = hand_value(self.dealer_hand)
		self.show_cards(chan, f'{bold}[Dealer]{bold} ({color(str(dtotal), light_blue)})', self.dealer_hand)

		if not all_busted:
			while dtotal < 17:
				card = self.shoe.draw()
				self.dealer_hand.append(card)
				dtotal = hand_value(self.dealer_hand)
				self.sendmsg(chan, f'{bold}[Dealer]{bold} hits {format_card(*card)} {sym_arrow} ({color(str(dtotal), light_blue)})')
			if len(self.dealer_hand) > 2:
				self.show_cards(chan, f'{bold}[Dealer]{bold} ({color(str(dtotal), light_blue)})', self.dealer_hand)

		dealer_bust = dtotal > 21
		dealer_bj   = len(self.dealer_hand) == 2 and dtotal == 21

		if dealer_bust:
			self.sendmsg(chan, f'{color("Dealer BUSTS!", red)}')
		elif dealer_bj:
			self.sendmsg(chan, f'{color("Dealer has BLACKJACK!", yellow)}')

		self.sendmsg(chan, f'{bold}{color(" RESULTS ", black, green)}{bold}')

		for p in self.players:
			if p.status == 'busted':
				new_chips = self.add_chips(p.nick, -p.bet)
				self.house_add(p.bet)
				self.sendmsg(chan, f' {color(sym_cross, red)} {c_nick(p.nick)} busted ({c_loss(p.bet)}) {sym_arrow} {c_money(new_chips)}')
			elif p.status == 'blackjack':
				if dealer_bj:
					chips = self.get_chips(p.nick)
					self.sendmsg(chan, f' {color(sym_dash, yellow)} {c_nick(p.nick)} push \u2014 both blackjack {sym_arrow} {c_money(chips)}')
				else:
					win = int(p.bet * 1.5)
					new_chips = self.add_chips(p.nick, win)
					self.house_add(-win)
					self.sendmsg(chan, f' {color(sym_star, green)} {c_nick(p.nick)} {color("BLACKJACK", green)} ({color("+", green)}{c_money(win)}) {sym_arrow} {c_money(new_chips)}')
			elif dealer_bust:
				new_chips = self.add_chips(p.nick, p.bet)
				self.house_add(-p.bet)
				self.sendmsg(chan, f' {color(sym_check, green)} {c_nick(p.nick)} wins ({color("+", green)}{c_money(p.bet)}) {sym_arrow} {c_money(new_chips)}')
			elif p.total > dtotal:
				new_chips = self.add_chips(p.nick, p.bet)
				self.house_add(-p.bet)
				self.sendmsg(chan, f' {color(sym_check, green)} {c_nick(p.nick)} wins {p.total} vs {dtotal} ({color("+", green)}{c_money(p.bet)}) {sym_arrow} {c_money(new_chips)}')
			elif p.total == dtotal:
				chips = self.get_chips(p.nick)
				self.sendmsg(chan, f' {color(sym_dash, yellow)} {c_nick(p.nick)} push {p.total} vs {dtotal} {sym_arrow} {c_money(chips)}')
			else:
				new_chips = self.add_chips(p.nick, -p.bet)
				self.house_add(p.bet)
				self.sendmsg(chan, f' {color(sym_cross, red)} {c_nick(p.nick)} loses {p.total} vs {dtotal} ({c_loss(p.bet)}) {sym_arrow} {c_money(new_chips)}')

		if self.db is not None:
			self.save_db()
		self.reset_game()

	def _move_timer(self, chan):
		while True:
			time.sleep(5)
			with self.lock:
				if self.state != 'playing':
					break
				if self.current_idx >= len(self.players):
					break
				if time.time() - self.last_move > MOVE_TIMEOUT:
					current = self.players[self.current_idx]
					if current.status == 'playing':
						current.status = 'busted'
						self.sendmsg(chan, f'{color("TIMEOUT!", red)} {c_nick(current.nick)} ran out of time and forfeits!')
						self._next_player(chan)

	# ──────────────────── Poker Logic ────────────────────

	def _pk_lobby_expired(self, chan):
		with self.lock:
			if self.pk_state == 'lobby':
				if len(self.pk_players) >= 2:
					self._pk_start_hand(chan)
				else:
					self.sendmsg(chan, 'Not enough players for poker. Table closed.')
					self._pk_reset()

	def _pk_start_hand(self, chan):
		self._check_shuffle(chan)
		self.pk_state       = 'playing'
		self.pk_street      = 'preflop'
		self.pk_community   = []
		self.pk_current_bet = 0
		self.pk_min_raise   = BIG_BLIND
		self.pk_cards_shown = False

		n = len(self.pk_players)
		for p in self.pk_players:
			p.hand      = []
			p.bet       = 0
			p.total_bet = 0
			p.folded    = False
			p.all_in    = False
			p.acted     = False

		sb_idx = self.pk_dealer_btn % n if n == 2 else (self.pk_dealer_btn + 1) % n
		bb_idx = (self.pk_dealer_btn + 1) % n if n == 2 else (self.pk_dealer_btn + 2) % n

		sb = self.pk_players[sb_idx]
		bb = self.pk_players[bb_idx]

		sb_chips = self.get_chips(sb.nick)
		bb_chips = self.get_chips(bb.nick)
		sb_amt   = min(SMALL_BLIND, sb_chips)
		bb_amt   = min(BIG_BLIND, bb_chips)

		sb.bet = sb_amt
		sb.total_bet = sb_amt
		if sb_amt >= sb_chips:
			sb.all_in = True
		bb.bet = bb_amt
		bb.total_bet = bb_amt
		if bb_amt >= bb_chips:
			bb.all_in = True

		self.pk_current_bet = bb_amt

		self.sendmsg(chan, f'{bold}{color(" DEALING POKER ", black, orange)}{bold}')
		self.sendmsg(chan, f'{c_nick(sb.nick)} posts small blind ({c_money(sb_amt)})')
		self.sendmsg(chan, f'{c_nick(bb.nick)} posts big blind ({c_money(bb_amt)})')

		for _ in range(2):
			for p in self.pk_players:
				p.hand.append(self.shoe.draw())

		for p in self.pk_players:
			try:
				self.show_cards(p.nick, 'Your hole cards:', p.hand)
			except Exception as ex:
				_log(f'[!] Failed to PM hole cards to {p.nick}: {ex}')
				traceback.print_exc()

		pot = sum(p.total_bet for p in self.pk_players)
		self.sendmsg(chan, f'Hole cards dealt \u2014 check your PMs! Pot: {c_money(pot)}')

		self.pk_last_move = time.time()
		threading.Thread(target=self._pk_move_timer, args=(chan,), daemon=True).start()

		if n == 2:
			start = self.pk_dealer_btn % n
		else:
			start = (bb_idx + 1) % n

		for i in range(n):
			idx = (start + i) % n
			if not self.pk_players[idx].folded and not self.pk_players[idx].all_in:
				self.pk_current_idx = idx
				break

		self._pk_prompt(chan)

	def _pk_prompt(self, chan, prefix=None):
		p = self.pk_players[self.pk_current_idx]
		pot = sum(pp.total_bet for pp in self.pk_players)
		to_call = self.pk_current_bet - p.bet
		self.pk_last_move = time.time()
		if to_call > 0:
			prompt = f'Pot: {c_money(pot)} {sep} {c_nick(p.nick)}: {c_cmd("!call")} {c_money(to_call)}, {c_cmd("!raise")} {c_arg("<total>")}, {c_cmd("!fold")}, or {c_cmd("!allin")}'
		else:
			prompt = f'Pot: {c_money(pot)} {sep} {c_nick(p.nick)}: {c_cmd("!check")}, {c_cmd("!raise")} {c_arg("<total>")}, or {c_cmd("!fold")}'
		if prefix:
			self.sendmsg(chan, f'{prefix} {sep} {prompt}')
		else:
			self.sendmsg(chan, prompt)

	def _pk_after_action(self, chan, prefix=None):
		active = [p for p in self.pk_players if not p.folded]
		if len(active) == 1:
			if prefix:
				self.sendmsg(chan, prefix)
			self._pk_win_by_fold(chan, active[0])
			return

		if self._pk_round_complete():
			if prefix:
				self.sendmsg(chan, prefix)
			self._pk_next_street(chan)
			return

		n = len(self.pk_players)
		for i in range(1, n + 1):
			idx = (self.pk_current_idx + i) % n
			p = self.pk_players[idx]
			if not p.folded and not p.all_in and not p.acted:
				self.pk_current_idx = idx
				self._pk_prompt(chan, prefix)
				return

		if prefix:
			self.sendmsg(chan, prefix)
		self._pk_next_street(chan)

	def _pk_round_complete(self):
		for p in self.pk_players:
			if not p.folded and not p.all_in:
				if not p.acted:
					return False
				if p.bet < self.pk_current_bet:
					return False
		return True

	def _pk_next_street(self, chan):
		for p in self.pk_players:
			p.bet   = 0
			p.acted = False
		self.pk_current_bet = 0
		self.pk_min_raise   = BIG_BLIND

		can_bet = [p for p in self.pk_players if not p.folded and not p.all_in]
		active  = [p for p in self.pk_players if not p.folded]

		if self.pk_street == 'preflop':
			self.pk_street = 'flop'
			for _ in range(3):
				self.pk_community.append(self.shoe.draw())
			self.sendmsg(chan, f'{bold}{color(" FLOP ", black, green)}{bold}')
			self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		elif self.pk_street == 'flop':
			self.pk_street = 'turn'
			self.pk_community.append(self.shoe.draw())
			self.sendmsg(chan, f'{bold}{color(" TURN ", black, green)}{bold}')
			self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		elif self.pk_street == 'turn':
			self.pk_street = 'river'
			self.pk_community.append(self.shoe.draw())
			self.sendmsg(chan, f'{bold}{color(" RIVER ", black, green)}{bold}')
			self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		elif self.pk_street == 'river':
			self._pk_showdown(chan)
			return

		if len(can_bet) <= 1 and len(active) > 1:
			if not self.pk_cards_shown:
				self.pk_cards_shown = True
				self.sendmsg(chan, f'{bold}Players are all-in \u2014 showing cards:{bold}')
				for p in active:
					self.sendmsg(chan, f' {bold}[{c_nick(p.nick)}]{bold} {format_card(*p.hand[0])} {format_card(*p.hand[1])}')
			self._pk_next_street(chan)
			return

		n = len(self.pk_players)
		start = (self.pk_dealer_btn + 1) % n
		for i in range(n):
			idx = (start + i) % n
			if not self.pk_players[idx].folded and not self.pk_players[idx].all_in:
				self.pk_current_idx = idx
				self._pk_prompt(chan)
				return

		self._pk_next_street(chan)

	def _pk_showdown(self, chan):
		pots  = poker_calculate_pots(self.pk_players)
		self.sendmsg(chan, f'{bold}{color(" SHOWDOWN ", black, orange)}{bold}')
		self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		self.blank(chan)

		evals = {}
		for p in self.pk_players:
			if not p.folded:
				rank = poker_best_hand(p.hand + self.pk_community)
				evals[p.nick] = rank
				name = poker_hand_name(rank)
				self.sendmsg(chan, f' {bold}[{c_nick(p.nick)}]{bold} {format_card(*p.hand[0])} {format_card(*p.hand[1])} \u2014 {color(name, yellow)}')
			else:
				self.sendmsg(chan, f' {bold}[{c_nick(p.nick)}]{bold} {color("folded", grey)}')

		self.blank(chan)

		winnings = {}
		for pot_amount, eligible in pots:
			best_rank = None
			for p in eligible:
				r = evals.get(p.nick)
				if r and (best_rank is None or r > best_rank):
					best_rank = r
			winners = [p for p in eligible if evals.get(p.nick) == best_rank]
			if winners:
				split     = pot_amount // len(winners)
				remainder = pot_amount % len(winners)
				for i, w in enumerate(winners):
					winnings[w.nick] = winnings.get(w.nick, 0) + split + (1 if i < remainder else 0)

		self.sendmsg(chan, f'{bold}{color(" RESULTS ", black, green)}{bold}')
		for p in self.pk_players:
			won = winnings.get(p.nick, 0)
			net = won - p.total_bet
			new_chips = self.add_chips(p.nick, net)
			if p.folded:
				if p.total_bet > 0:
					self.sendmsg(chan, f' {color(sym_cross, red)} {c_nick(p.nick)} folded ({c_loss(p.total_bet)}) {sym_arrow} {c_money(new_chips)}')
				else:
					self.sendmsg(chan, f' {color(sym_cross, red)} {c_nick(p.nick)} folded {sym_arrow} {c_money(new_chips)}')
			elif net > 0:
				name = poker_hand_name(evals.get(p.nick, (0,)))
				self.sendmsg(chan, f' {color(sym_star, green)} {c_nick(p.nick)} wins {c_money(won)} ({name}) ({color("+", green)}{c_money(net)}) {sym_arrow} {c_money(new_chips)}')
			elif net == 0:
				self.sendmsg(chan, f' {color(sym_dash, yellow)} {c_nick(p.nick)} breaks even {sym_arrow} {c_money(new_chips)}')
			else:
				self.sendmsg(chan, f' {color(sym_cross, red)} {c_nick(p.nick)} loses ({c_loss(abs(net))}) {sym_arrow} {c_money(new_chips)}')

		if self.db is not None:
			self.save_db()
		self._pk_reset()

	def _pk_win_by_fold(self, chan, winner):
		total_pot = sum(p.total_bet for p in self.pk_players)
		self.sendmsg(chan, f'{c_nick(winner.nick)} wins {c_money(total_pot)} \u2014 everyone else folded!')

		for p in self.pk_players:
			if p.nick == winner.nick:
				net = total_pot - p.total_bet
			else:
				net = -p.total_bet
			if net != 0:
				self.add_chips(p.nick, net)

		if self.db is not None:
			self.save_db()
		self._pk_reset()

	def _pk_move_timer(self, chan):
		while True:
			time.sleep(5)
			with self.lock:
				if self.pk_state != 'playing':
					break
				if time.time() - self.pk_last_move > MOVE_TIMEOUT:
					current = self.pk_players[self.pk_current_idx]
					if not current.folded and not current.all_in and not current.acted:
						current.folded = True
						self.sendmsg(chan, f'{color("TIMEOUT!", red)} {c_nick(current.nick)} ran out of time \u2014 hand folded!')
						self._pk_after_action(chan)

	def _pk_reset(self):
		self.pk_state       = 'idle'
		self.pk_street      = None
		self.pk_players     = []
		self.pk_community   = []
		self.pk_current_idx = 0
		self.pk_current_bet = 0
		self.pk_min_raise   = BIG_BLIND
		self.pk_last_move   = 0
		self.pk_cards_shown = False

	# ──────────────────── Shared Logic ────────────────────

	def player_left(self, nick, chan):
		with self.lock:
			if self.state == 'lobby':
				idx = next((i for i, p in enumerate(self.players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.players.pop(idx)
					self.sendmsg(chan, f'{c_nick(nick)} left the table. [{len(self.players)}/{MAX_PLAYERS}]')
					if not self.players:
						if self.lobby_timer:
							self.lobby_timer.cancel()
						self.state = 'idle'
						self.sendmsg(chan, 'Table closed \u2014 no players remain.')
			elif self.state == 'playing':
				for i, p in enumerate(self.players):
					if p.nick.lower() == nick.lower() and p.status == 'playing':
						p.status = 'busted'
						self.add_chips(p.nick, -p.bet)
						self.house_add(p.bet)
						self.sendmsg(chan, f'{c_nick(nick)} left \u2014 {c_loss(p.bet)} forfeited to the house.')
						if i == self.current_idx:
							self._next_player(chan)
						break

			if self.pk_state == 'lobby':
				idx = next((i for i, p in enumerate(self.pk_players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.pk_players.pop(idx)
					self.sendmsg(chan, f'{c_nick(nick)} left the poker table. [{len(self.pk_players)}/{MAX_PLAYERS}]')
					if not self.pk_players:
						if self.pk_lobby_timer:
							self.pk_lobby_timer.cancel()
						self._pk_reset()
						self.sendmsg(chan, 'Poker table closed.')
			elif self.pk_state == 'playing':
				for i, p in enumerate(self.pk_players):
					if p.nick.lower() == nick.lower() and not p.folded:
						p.folded = True
						if p.total_bet > 0:
							self.sendmsg(chan, f'{c_nick(nick)} left \u2014 poker hand folded, {c_loss(p.total_bet)} forfeited to the pot.')
						else:
							self.sendmsg(chan, f'{c_nick(nick)} left \u2014 poker hand folded.')
						active = [pp for pp in self.pk_players if not pp.folded]
						if len(active) == 1:
							self._pk_win_by_fold(chan, active[0])
						elif i == self.pk_current_idx:
							self._pk_after_action(chan)
						break

	def reset_game(self):
		self.state       = 'idle'
		self.players     = []
		self.dealer_hand = []
		self.current_idx = 0
		self.last_move   = 0


BlackJack = IRC()
