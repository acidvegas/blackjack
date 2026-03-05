#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# irc.py

import os
import random
import socket
import ssl
import threading
import time

import config
import debug

try:
	from pickledb import PickleDB
except ImportError:
	raise SystemExit('pickledb is required: pip install pickledb')

NUM_DECKS        = 6
MAX_PLAYERS      = 7
DEFAULT_BET      = 100
MIN_BET          = 10
MAX_BET          = 50000
STARTING_CHIPS   = 1000
MOVE_TIMEOUT     = 300
LOBBY_TIMEOUT    = 30
DB_SYNC_INTERVAL = 300
RESET_COOLDOWN   = 86400

SUITS = {
	'hearts'   : ('♥', True),
	'diamonds' : ('♦', True),
	'clubs'    : ('♣', False),
	'spades'   : ('♠', False),
}
RANKS       = ['A','2','3','4','5','6','7','8','9','10','J','Q','K']
RANK_VALUES = {'A':11, '2':2, '3':3, '4':4, '5':5, '6':6, '7':7, '8':8, '9':9, '10':10, 'J':10, 'Q':10, 'K':10}

CARD_ART = {
	'A'  : ('A      ','       ','   X   ','       ','      A'),
	'2'  : ('2      ','   X   ','       ','   X   ','      2'),
	'3'  : ('3      ','   X   ','   X   ','   X   ','      3'),
	'4'  : ('4      ','  X X  ','       ','  X X  ','      4'),
	'5'  : ('5      ','  X X  ','   X   ','  X X  ','      5'),
	'6'  : ('6      ','  X X  ','  X X  ','  X X  ','      6'),
	'7'  : ('7      ','  X X  ','  XXX  ','  X X  ','      7'),
	'8'  : ('8      ','  XXX  ','  X X  ','  XXX  ','      8'),
	'9'  : ('9      ','  XXX  ','  XXX  ','  XXX  ','      9'),
	'10' : ('10     ','  XXX  ',' XX XX ','  XXX  ','     10'),
	'J'  : ('J      ','       ','   X   ','       ','      J'),
	'Q'  : ('Q      ','       ','   X   ','       ','      Q'),
	'K'  : ('K      ','       ','   X   ','       ','      K'),
}
FACEDOWN = ('\u2593' * 7,) * 5

bold       = '\x02'
reset      = '\x0f'
sym_arrow  = '\u2192'
sym_check  = '\u2713'
sym_cross  = '\u2717'
sym_dash   = '\u2500'
sym_star   = '\u2605'
white      = '00'
black      = '01'
blue       = '02'
green      = '03'
red        = '04'
orange     = '07'
yellow     = '08'
light_blue = '12'
grey       = '14'


def color(msg, foreground, background=None):
	if background:
		return f'\x03{foreground},{background}{msg}{reset}'
	return f'\x03{foreground}{msg}{reset}'


def hand_value(cards):
	total = sum(RANK_VALUES[rank] for rank, _ in cards)
	aces  = sum(1 for rank, _ in cards if rank == 'A')
	while total > 21 and aces:
		total -= 10
		aces  -= 1
	return total


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


class Shoe:
	def __init__(self, num_decks=6):
		self.num_decks = num_decks
		self.cards     = []
		self.shuffle()

	def shuffle(self):
		self.cards = [(rank, suit) for _ in range(self.num_decks) for suit in SUITS for rank in RANKS]
		random.shuffle(self.cards)

	def draw(self):
		if len(self.cards) < 30:
			self.shuffle()
		return self.cards.pop()


class Player:
	def __init__(self, nick, bet):
		self.nick   = nick
		self.bet    = bet
		self.hand   = []
		self.status = 'playing'

	@property
	def total(self):
		return hand_value(self.hand)

	@property
	def is_blackjack(self):
		return len(self.hand) == 2 and self.total == 21


class IRC:
	def __init__(self):
		self.sock        = None
		self.db          = None
		self.shoe        = Shoe(NUM_DECKS)
		self.state       = 'idle'
		self.players     = []
		self.dealer_hand = []
		self.current_idx = 0
		self.last_move   = 0
		self.lobby_timer = None
		self.lock        = threading.Lock()

	# --- IRC Protocol ---

	def connect(self):
		try:
			self.create_socket()
			self.sock.connect((config.connection.server, config.connection.port))
			if config.login.network:
				self.raw('PASS ' + config.login.network)
			self.raw(f'USER {config.ident.username} 0 * :{config.ident.realname}')
			self.raw('NICK ' + config.ident.nickname)
		except socket.error as ex:
			debug.error('Failed to connect to IRC server.', ex)
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
		self.sock.send(bytes(msg + '\r\n', 'utf-8'))

	def sendmsg(self, target, msg):
		self.raw(f'PRIVMSG {target} :{msg}')
		time.sleep(0.4)

	def show_cards(self, chan, label, cards, hide_first=False):
		lines = render_hand(cards, hide_first)
		self.sendmsg(chan, label)
		for line in lines:
			self.raw(f'PRIVMSG {chan} :{line}')
			time.sleep(0.1)

	def action(self, chan, msg):
		self.sendmsg(chan, f'\x01ACTION {msg}\x01')

	def join(self, chan, key=None):
		self.raw(f'JOIN {chan} {key}') if key else self.raw(f'JOIN {chan}')

	def identify(self, username, password):
		self.sendmsg('NickServ', f'IDENTIFY {username} {password}')

	def listen(self):
		while True:
			try:
				data = self.sock.recv(4096).decode('utf-8')
				if data:
					for line in (l for l in data.split('\r\n') if l):
						debug.irc(line)
						if line.startswith('ERROR :Closing Link:'):
							raise Exception('Connection has closed.')
						elif len(line.split()) >= 2:
							self.handle_events(line)
				else:
					debug.error('No data received from server.')
					break
			except (UnicodeDecodeError, UnicodeEncodeError):
				debug.error('Unicode error occurred.')
			except Exception as ex:
				debug.error('Unexpected error occurred.', ex)
				break
		self.event_disconnect()

	def handle_events(self, data):
		args = data.split()
		if args[0] == 'PING':
			self.raw('PONG ' + args[1][1:])
		elif args[1] == '001':
			self.event_connect()
		elif args[1] == '433':
			debug.error_exit('Nickname is already in use.')
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

	# --- Events ---

	def event_connect(self):
		self.load_db()
		if config.login.nickserv:
			self.identify(config.ident.username, config.login.nickserv)
		if config.login.operator:
			self.raw(f'OPER {config.ident.username} {config.login.operator}')
		self.join(config.connection.channel, config.connection.key)

	def event_disconnect(self):
		if self.db:
			self.db.save()
		self.sock.close()
		self.reset_game()
		time.sleep(10)
		self.connect()

	def event_kick(self, nick, chan, kicked):
		if kicked == config.ident.nickname and chan == config.connection.channel:
			time.sleep(3)
			self.join(config.connection.channel, config.connection.key)
		else:
			self.player_left(kicked, chan)

	def event_part(self, nick, chan):
		if chan == config.connection.channel:
			self.player_left(nick, chan)

	def event_quit(self, nick):
		self.player_left(nick, config.connection.channel)

	def event_message(self, nick, chan, msg):
		if chan != config.connection.channel:
			return
		parts = msg.split()
		if not parts:
			return
		cmd = parts[0].lower()
		if   cmd in ('!blackjack', '!bj'):  self.cmd_blackjack(nick, chan, parts[1:])
		elif cmd == '!deal':                 self.cmd_deal(nick, chan)
		elif cmd == '!hit':                  self.cmd_hit(nick, chan)
		elif cmd == '!stand':                self.cmd_stand(nick, chan)
		elif cmd == '!leave':                self.cmd_leave(nick, chan)
		elif cmd == '!chips':                self.cmd_chips(nick, chan)
		elif cmd == '!top':                  self.cmd_top(nick, chan)
		elif cmd == '!help':                 self.cmd_help(nick, chan)

	# --- Database ---

	def load_db(self):
		db_path = os.path.join('data', 'chips.json')
		os.makedirs('data', exist_ok=True)
		self.db = PickleDB(db_path)
		self.db.load()
		threading.Thread(target=self._db_sync_loop, daemon=True).start()
		debug.irc('Chip database loaded.')

	def _db_sync_loop(self):
		while True:
			time.sleep(DB_SYNC_INTERVAL)
			if self.db:
				self.db.save()
				debug.irc('Database synced to disk.')

	def get_player_data(self, nick):
		key  = nick.lower()
		data = self.db.get(key)
		if data is None:
			data = {'chips': STARTING_CHIPS, 'last_reset': 0}
			self.db.set(key, data)
		return data

	def set_player_data(self, nick, data):
		self.db.set(nick.lower(), data)

	def get_chips(self, nick):
		return self.get_player_data(nick)['chips']

	def add_chips(self, nick, amount):
		data = self.get_player_data(nick)
		data['chips'] = max(0, data['chips'] + amount)
		self.set_player_data(nick, data)
		return data['chips']

	# --- Commands ---

	def cmd_blackjack(self, nick, chan, args):
		with self.lock:
			bet = DEFAULT_BET
			if args:
				try:
					bet = int(args[0])
				except ValueError:
					self.sendmsg(chan, f'{color("ERROR", red)} Invalid bet amount.')
					return
				if bet < MIN_BET:
					self.sendmsg(chan, f'{color("ERROR", red)} Minimum bet is ${MIN_BET}.')
					return
				if bet > MAX_BET:
					self.sendmsg(chan, f'{color("ERROR", red)} Maximum bet is ${MAX_BET:,}.')
					return

			chips = self.get_chips(nick)
			if chips < bet:
				self.sendmsg(chan, f'{color("ERROR", red)} {nick}: you have ${chips:,}. Use {bold}!chips{bold} to reset if broke.')
				return

			if self.state == 'idle':
				self.state   = 'lobby'
				self.players = [Player(nick, bet)]
				self.dealer_hand = []
				self.current_idx = 0
				self.sendmsg(chan, f'{bold}{color(" ♠ ♥  BLACKJACK  ♦ ♣ ", black, green)}{bold}')
				self.sendmsg(chan, f'{nick} opened a table! Type {bold}!blackjack [bet]{bold} to join or {bold}!deal{bold} to start.')
				self.sendmsg(chan, f'{nick} bets {bold}${bet:,}{bold} — {LOBBY_TIMEOUT}s until auto-deal')
				self.lobby_timer = threading.Timer(LOBBY_TIMEOUT, self._lobby_expired, [chan])
				self.lobby_timer.daemon = True
				self.lobby_timer.start()

			elif self.state == 'lobby':
				if any(p.nick.lower() == nick.lower() for p in self.players):
					self.sendmsg(chan, f'{nick}: you are already at the table.')
					return
				if len(self.players) >= MAX_PLAYERS:
					self.sendmsg(chan, f'{color("ERROR", red)} Table is full ({MAX_PLAYERS} players).')
					return
				self.players.append(Player(nick, bet))
				self.sendmsg(chan, f'{nick} joins the table! ({bold}${bet:,}{bold} bet) [{len(self.players)}/{MAX_PLAYERS}]')
				if len(self.players) >= MAX_PLAYERS:
					if self.lobby_timer:
						self.lobby_timer.cancel()
					self._start_round(chan)

			elif self.state == 'playing':
				self.sendmsg(chan, f'{color("ERROR", red)} A round is in progress. Wait for it to finish.')

	def cmd_deal(self, nick, chan):
		with self.lock:
			if self.state != 'lobby':
				return
			if nick.lower() != self.players[0].nick.lower():
				self.sendmsg(chan, f'{color("ERROR", red)} Only {self.players[0].nick} can start the deal.')
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
					self.sendmsg(chan, f"{nick}: it's {bold}{current.nick}'s{bold} turn.")
				return

			card = self.shoe.draw()
			current.hand.append(card)
			self.last_move = time.time()

			if current.total > 21:
				current.status = 'busted'
				self.show_cards(chan, f'{bold}[{current.nick}]{bold} ({color(str(current.total), light_blue)}) — {color("BUST!", red)}', current.hand)
				self._next_player(chan)
			elif current.total == 21:
				current.status = 'stood'
				self.show_cards(chan, f'{bold}[{current.nick}]{bold} ({color(str(current.total), light_blue)}) — {color("21!", green)}', current.hand)
				self._next_player(chan)
			else:
				self.show_cards(chan, f'{bold}[{current.nick}]{bold} ({color(str(current.total), light_blue)})', current.hand)

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
			self.sendmsg(chan, f'{current.nick} stands at {bold}{current.total}{bold}.')
			self._next_player(chan)

	def cmd_leave(self, nick, chan):
		with self.lock:
			if self.state == 'lobby':
				idx = next((i for i, p in enumerate(self.players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.players.pop(idx)
					self.sendmsg(chan, f'{nick} left the table. [{len(self.players)}/{MAX_PLAYERS}]')
					if not self.players:
						if self.lobby_timer:
							self.lobby_timer.cancel()
						self.state = 'idle'
						self.sendmsg(chan, 'Table closed — no players remain.')

	def cmd_chips(self, nick, chan):
		data  = self.get_player_data(nick)
		chips = data['chips']
		if chips <= 0:
			elapsed = time.time() - data.get('last_reset', 0)
			if elapsed >= RESET_COOLDOWN:
				data['chips']      = STARTING_CHIPS
				data['last_reset'] = time.time()
				self.set_player_data(nick, data)
				self.sendmsg(chan, f'{nick} has been given {bold}${STARTING_CHIPS:,}{bold} in chips. Good luck!')
			else:
				remaining = RESET_COOLDOWN - elapsed
				hours     = int(remaining // 3600)
				minutes   = int((remaining % 3600) // 60)
				self.sendmsg(chan, f'{nick}: you are broke. Reset available in {bold}{hours}h {minutes}m{bold}.')
		else:
			self.sendmsg(chan, f'{nick} has {bold}${chips:,}{bold} in chips.')

	def cmd_top(self, nick, chan):
		all_keys = self.db.all()
		if not all_keys:
			self.sendmsg(chan, 'No players registered yet.')
			return
		leaderboard = []
		for key in all_keys:
			data = self.db.get(key)
			if isinstance(data, dict) and 'chips' in data:
				leaderboard.append((key, data['chips']))
		leaderboard.sort(key=lambda x: x[1], reverse=True)
		self.sendmsg(chan, f'{bold}{color(" TOP 10 ", black, green)}{bold}')
		for i, (name, chips) in enumerate(leaderboard[:10], 1):
			self.sendmsg(chan, f' {bold}#{i}{bold} {name} — ${chips:,}')

	def cmd_help(self, nick, chan):
		self.sendmsg(chan, f'{bold}{color(" BLACKJACK COMMANDS ", black, green)}{bold}')
		self.sendmsg(chan, f' {bold}!blackjack [bet]{bold} — Start or join a game (default bet: ${DEFAULT_BET})')
		self.sendmsg(chan, f' {bold}!deal{bold} — Force deal early (table opener only)')
		self.sendmsg(chan, f' {bold}!hit{bold}  — Draw another card')
		self.sendmsg(chan, f' {bold}!stand{bold} — Keep your hand')
		self.sendmsg(chan, f' {bold}!leave{bold} — Leave the lobby before deal')
		self.sendmsg(chan, f' {bold}!chips{bold} — Check your chips (resets to ${STARTING_CHIPS:,} if broke, 24h cooldown)')
		self.sendmsg(chan, f' {bold}!top{bold} — Leaderboard (top 10)')

	# --- Game Logic ---

	def _lobby_expired(self, chan):
		with self.lock:
			if self.state == 'lobby':
				self._start_round(chan)

	def _start_round(self, chan):
		self.state       = 'playing'
		self.current_idx = 0
		self.dealer_hand = []

		for _ in range(2):
			for player in self.players:
				player.hand.append(self.shoe.draw())
			self.dealer_hand.append(self.shoe.draw())

		self.sendmsg(chan, f'{bold}{color(" CARDS DEALT ", black, orange)}{bold}')
		self.show_cards(chan, f'{bold}[Dealer]{bold}', self.dealer_hand, hide_first=True)

		for player in self.players:
			bj = ''
			if player.is_blackjack:
				player.status = 'blackjack'
				bj = f' {color("BLACKJACK!", green)}'
			self.show_cards(chan, f'{bold}[{player.nick}]{bold} ({color(str(player.total), light_blue)}){bj}', player.hand)

		self.last_move = time.time()
		threading.Thread(target=self._move_timer, args=(chan,), daemon=True).start()
		self._advance(chan)

	def _advance(self, chan):
		while self.current_idx < len(self.players):
			if self.players[self.current_idx].status == 'playing':
				p = self.players[self.current_idx]
				self.last_move = time.time()
				self.sendmsg(chan, f"{p.nick}: your turn — {bold}!hit{bold} or {bold}!stand{bold}")
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
				time.sleep(1)
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
				self.sendmsg(chan, f' {color(sym_cross, red)} {bold}{p.nick}{bold} busted ({bold}-${p.bet:,}{bold}) {sym_arrow} ${new_chips:,}')
			elif p.status == 'blackjack':
				if dealer_bj:
					chips = self.get_chips(p.nick)
					self.sendmsg(chan, f' {color(sym_dash, yellow)} {bold}{p.nick}{bold} push — both blackjack {sym_arrow} ${chips:,}')
				else:
					win = int(p.bet * 1.5)
					new_chips = self.add_chips(p.nick, win)
					self.sendmsg(chan, f' {color(sym_star, green)} {bold}{p.nick}{bold} {color("BLACKJACK", green)} ({bold}+${win:,}{bold}) {sym_arrow} ${new_chips:,}')
			elif dealer_bust:
				new_chips = self.add_chips(p.nick, p.bet)
				self.sendmsg(chan, f' {color(sym_check, green)} {bold}{p.nick}{bold} wins ({bold}+${p.bet:,}{bold}) {sym_arrow} ${new_chips:,}')
			elif p.total > dtotal:
				new_chips = self.add_chips(p.nick, p.bet)
				self.sendmsg(chan, f' {color(sym_check, green)} {bold}{p.nick}{bold} wins {p.total} vs {dtotal} ({bold}+${p.bet:,}{bold}) {sym_arrow} ${new_chips:,}')
			elif p.total == dtotal:
				chips = self.get_chips(p.nick)
				self.sendmsg(chan, f' {color(sym_dash, yellow)} {bold}{p.nick}{bold} push {p.total} vs {dtotal} {sym_arrow} ${chips:,}')
			else:
				new_chips = self.add_chips(p.nick, -p.bet)
				self.sendmsg(chan, f' {color(sym_cross, red)} {bold}{p.nick}{bold} loses {p.total} vs {dtotal} ({bold}-${p.bet:,}{bold}) {sym_arrow} ${new_chips:,}')

		if self.db:
			self.db.save()

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
						self.sendmsg(chan, f'{color("TIMEOUT!", red)} {current.nick} ran out of time and forfeits!')
						self._next_player(chan)

	def player_left(self, nick, chan):
		with self.lock:
			if self.state == 'lobby':
				idx = next((i for i, p in enumerate(self.players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.players.pop(idx)
					self.sendmsg(chan, f'{nick} left the table. [{len(self.players)}/{MAX_PLAYERS}]')
					if not self.players:
						if self.lobby_timer:
							self.lobby_timer.cancel()
						self.state = 'idle'
						self.sendmsg(chan, 'Table closed — no players remain.')
			elif self.state == 'playing':
				for i, p in enumerate(self.players):
					if p.nick.lower() == nick.lower() and p.status == 'playing':
						p.status = 'busted'
						self.sendmsg(chan, f'{nick} left — hand forfeited.')
						if i == self.current_idx:
							self._next_player(chan)
						break

	def reset_game(self):
		self.state       = 'idle'
		self.players     = []
		self.dealer_hand = []
		self.current_idx = 0
		self.last_move   = 0


BlackJack = IRC()
