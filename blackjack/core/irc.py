#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# irc.py

import os
import random
import socket
import ssl
import threading
import time
from collections import Counter
from itertools import combinations

import config
import debug

try:
	from pickledb import PickleDB
except ImportError:
	raise SystemExit('pickledb is required: pip install pickledb')

# --- Game Constants ---
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
SMALL_BLIND      = 5
BIG_BLIND        = 10

# --- Card Data ---
SUITS = {
	'hearts'   : ('\u2764', True),
	'diamonds' : ('\u2666', False),
	'clubs'    : ('\u2663', False),
	'spades'   : ('\u2660', False),
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

# --- IRC Formatting ---
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


# --- Blackjack Hand Value ---
def hand_value(cards):
	total = sum(RANK_VALUES[rank] for rank, _ in cards)
	aces  = sum(1 for rank, _ in cards if rank == 'A')
	while total > 21 and aces:
		total -= 10
		aces  -= 1
	return total


# --- Card Formatting ---
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


# --- Poker Hand Evaluation ---
POKER_VALUES = {'A':14,'K':13,'Q':12,'J':11,'10':10,'9':9,'8':8,'7':7,'6':6,'5':5,'4':4,'3':3,'2':2}

HAND_NAMES = {
	8: 'Straight Flush', 7: 'Four of a Kind', 6: 'Full House',
	5: 'Flush', 4: 'Straight', 3: 'Three of a Kind',
	2: 'Two Pair', 1: 'Pair', 0: 'High Card',
}

RANK_NAMES = {
	14:'Ace', 13:'King', 12:'Queen', 11:'Jack', 10:'Ten',
	9:'Nine', 8:'Eight', 7:'Seven', 6:'Six', 5:'Five',
	4:'Four', 3:'Three', 2:'Two',
}

RANK_NAMES_PLURAL = {
	14:'Aces', 13:'Kings', 12:'Queens', 11:'Jacks', 10:'Tens',
	9:'Nines', 8:'Eights', 7:'Sevens', 6:'Sixes', 5:'Fives',
	4:'Fours', 3:'Threes', 2:'Twos',
}


def _rname(val, plural=False):
	m = RANK_NAMES_PLURAL if plural else RANK_NAMES
	return m.get(val, str(val))


def poker_rank_five(five_cards):
	values = sorted([POKER_VALUES[c[0]] for c in five_cards], reverse=True)
	suits  = [c[1] for c in five_cards]
	is_flush = len(set(suits)) == 1
	unique   = sorted(set(values), reverse=True)

	is_straight = False
	high = values[0]
	if len(unique) == 5:
		if unique[0] - unique[4] == 4:
			is_straight = True
			high = unique[0]
		elif unique == [14, 5, 4, 3, 2]:
			is_straight = True
			high = 5

	counts = Counter(values)
	groups = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
	freq   = [g[1] for g in groups]
	gv     = [g[0] for g in groups]

	if is_straight and is_flush:  return (8, high)
	if freq == [4, 1]:            return (7, gv[0], gv[1])
	if freq == [3, 2]:            return (6, gv[0], gv[1])
	if is_flush:                  return (5,) + tuple(values)
	if is_straight:               return (4, high)
	if freq == [3, 1, 1]:         return (3, gv[0], gv[1], gv[2])
	if freq == [2, 2, 1]:         return (2, gv[0], gv[1], gv[2])
	if freq == [2, 1, 1, 1]:      return (1, gv[0], gv[1], gv[2], gv[3])
	return (0,) + tuple(values)


def poker_best_hand(seven_cards):
	best = None
	for combo in combinations(seven_cards, 5):
		rank = poker_rank_five(combo)
		if best is None or rank > best:
			best = rank
	return best


def poker_hand_name(rank_tuple):
	cat = rank_tuple[0]
	if cat == 8:
		return 'Royal Flush' if rank_tuple[1] == 14 else f'Straight Flush, {_rname(rank_tuple[1])}-high'
	if cat == 7: return f'Four of a Kind, {_rname(rank_tuple[1], True)}'
	if cat == 6: return f'Full House, {_rname(rank_tuple[1], True)} full of {_rname(rank_tuple[2], True)}'
	if cat == 5: return f'Flush, {_rname(rank_tuple[1])}-high'
	if cat == 4: return f'Straight, {_rname(rank_tuple[1])}-high'
	if cat == 3: return f'Three of a Kind, {_rname(rank_tuple[1], True)}'
	if cat == 2: return f'Two Pair, {_rname(rank_tuple[1], True)} and {_rname(rank_tuple[2], True)}'
	if cat == 1: return f'Pair of {_rname(rank_tuple[1], True)}'
	return f'{_rname(rank_tuple[1])}-high'


def poker_calculate_pots(players):
	bet_levels = sorted(set(p.total_bet for p in players if p.total_bet > 0))
	pots = []
	prev = 0
	for level in bet_levels:
		contributors = [p for p in players if p.total_bet >= level]
		amount       = (level - prev) * len(contributors)
		eligible     = [p for p in contributors if not p.folded]
		if amount > 0 and eligible:
			pots.append((amount, eligible))
		prev = level
	return pots


# --- Classes ---

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


class PokerPlayer:
	def __init__(self, nick):
		self.nick      = nick
		self.hand      = []
		self.bet       = 0
		self.total_bet = 0
		self.folded    = False
		self.all_in    = False
		self.acted     = False


# --- IRC Bot ---

class IRC:
	def __init__(self):
		self.sock = None
		self.db   = None
		self.shoe = Shoe(NUM_DECKS)
		self.lock = threading.Lock()

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

	# ──────────────────── Events ────────────────────

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
		self._pk_reset()
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

		# Blackjack
		if   cmd in ('!blackjack', '!bj'):   self.cmd_blackjack(nick, chan, parts[1:])
		elif cmd == '!hit':                   self.cmd_hit(nick, chan)
		elif cmd == '!stand':                 self.cmd_stand(nick, chan)
		elif cmd in ('!double', '!dd'):       self.cmd_double(nick, chan)

		# Poker
		elif cmd in ('!poker', '!pk'):        self.cmd_poker(nick, chan)
		elif cmd == '!fold':                  self.cmd_fold(nick, chan)
		elif cmd == '!check':                 self.cmd_check(nick, chan)
		elif cmd == '!call':                  self.cmd_call(nick, chan)
		elif cmd in ('!raise', '!bet'):       self.cmd_raise(nick, chan, parts[1:])
		elif cmd == '!allin':                 self.cmd_allin(nick, chan)

		# Shared
		elif cmd == '!deal':
			if self.state == 'lobby':         self.cmd_deal(nick, chan)
			elif self.pk_state == 'lobby':    self.cmd_pk_deal(nick, chan)
		elif cmd == '!leave':                 self.cmd_leave(nick, chan)
		elif cmd == '!chips':                 self.cmd_chips(nick, chan)
		elif cmd == '!top':                   self.cmd_top(nick, chan)
		elif cmd == '!help':                  self.cmd_help(nick, chan)

	# ──────────────────── Database ────────────────────

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

	# ──────────────────── Blackjack Commands ────────────────────

	def cmd_blackjack(self, nick, chan, args):
		with self.lock:
			if self.pk_state != 'idle':
				self.sendmsg(chan, f'{color("ERROR", red)} Wait for the poker game to finish first.')
				return

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
				self.sendmsg(chan, f'{bold}{color(" \u2660 \u2764  BLACKJACK  \u2666 \u2663 ", white, green)}{bold}')
				self.sendmsg(chan, f'{nick} opened a table! Type {bold}!blackjack [bet]{bold} to join or {bold}!deal{bold} to start.')
				self.sendmsg(chan, f'{nick} bets {bold}${bet:,}{bold} \u2014 {LOBBY_TIMEOUT}s until auto-deal')
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
				self.show_cards(chan, f'{bold}[{current.nick}]{bold} ({color(str(current.total), light_blue)}) \u2014 {color("BUST!", red)}', current.hand)
				self._next_player(chan)
			elif current.total == 21:
				current.status = 'stood'
				self.show_cards(chan, f'{bold}[{current.nick}]{bold} ({color(str(current.total), light_blue)}) \u2014 {color("21!", green)}', current.hand)
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
				self.sendmsg(chan, f'{color("ERROR", red)} Need ${current.bet * 2:,} to double down (you have ${chips:,}).')
				return

			current.bet *= 2
			card = self.shoe.draw()
			current.hand.append(card)
			self.last_move = time.time()
			self.sendmsg(chan, f'{bold}{current.nick}{bold} doubles down! Bet is now {bold}${current.bet:,}{bold}')

			if current.total > 21:
				current.status = 'busted'
				self.show_cards(chan, f'{bold}[{current.nick}]{bold} ({color(str(current.total), light_blue)}) \u2014 {color("BUST!", red)}', current.hand)
			else:
				current.status = 'stood'
				self.show_cards(chan, f'{bold}[{current.nick}]{bold} ({color(str(current.total), light_blue)})', current.hand)
			self._next_player(chan)

	# ──────────────────── Poker Commands ────────────────────

	def cmd_poker(self, nick, chan):
		with self.lock:
			if self.state != 'idle':
				self.sendmsg(chan, f'{color("ERROR", red)} Wait for the blackjack game to finish first.')
				return

			if self.pk_state == 'idle':
				chips = self.get_chips(nick)
				if chips < BIG_BLIND:
					self.sendmsg(chan, f'{color("ERROR", red)} {nick}: need at least ${BIG_BLIND} to play. Use {bold}!chips{bold}.')
					return
				self.pk_state   = 'lobby'
				self.pk_players = [PokerPlayer(nick)]
				self.pk_community   = []
				self.pk_current_bet = 0
				self.pk_cards_shown = False
				self.sendmsg(chan, f'{bold}{color(" \u2660 \u2764  TEXAS HOLD\'EM  \u2666 \u2663 ", white, green)}{bold}')
				self.sendmsg(chan, f'{nick} opened a poker table! Type {bold}!poker{bold} to join or {bold}!deal{bold} to start.')
				self.sendmsg(chan, f'Blinds: ${SMALL_BLIND}/${BIG_BLIND} \u2014 {LOBBY_TIMEOUT}s until auto-deal')
				self.pk_lobby_timer = threading.Timer(LOBBY_TIMEOUT, self._pk_lobby_expired, [chan])
				self.pk_lobby_timer.daemon = True
				self.pk_lobby_timer.start()

			elif self.pk_state == 'lobby':
				if any(p.nick.lower() == nick.lower() for p in self.pk_players):
					self.sendmsg(chan, f'{nick}: you are already at the table.')
					return
				if len(self.pk_players) >= MAX_PLAYERS:
					self.sendmsg(chan, f'{color("ERROR", red)} Table is full ({MAX_PLAYERS} players).')
					return
				chips = self.get_chips(nick)
				if chips < BIG_BLIND:
					self.sendmsg(chan, f'{color("ERROR", red)} {nick}: need at least ${BIG_BLIND} to play.')
					return
				self.pk_players.append(PokerPlayer(nick))
				self.sendmsg(chan, f'{nick} joins the table! [{len(self.pk_players)}/{MAX_PLAYERS}]')
				if len(self.pk_players) >= MAX_PLAYERS:
					if self.pk_lobby_timer:
						self.pk_lobby_timer.cancel()
					self._pk_start_hand(chan)

			elif self.pk_state == 'playing':
				self.sendmsg(chan, f'{color("ERROR", red)} A poker hand is in progress. Wait for it to finish.')

	def cmd_pk_deal(self, nick, chan):
		with self.lock:
			if self.pk_state != 'lobby':
				return
			if len(self.pk_players) < 2:
				self.sendmsg(chan, f'{color("ERROR", red)} Need at least 2 players to start.')
				return
			if nick.lower() != self.pk_players[0].nick.lower():
				self.sendmsg(chan, f'{color("ERROR", red)} Only {self.pk_players[0].nick} can start the deal.')
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
			self.sendmsg(chan, f'{bold}{current.nick}{bold} folds.')
			self._pk_after_action(chan)

	def cmd_check(self, nick, chan):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return
			if current.bet < self.pk_current_bet:
				to_call = self.pk_current_bet - current.bet
				self.sendmsg(chan, f'{color("ERROR", red)} Cannot check \u2014 ${to_call} to call. Use {bold}!call{bold}, {bold}!raise{bold}, or {bold}!fold{bold}.')
				return
			current.acted = True
			self.sendmsg(chan, f'{bold}{current.nick}{bold} checks.')
			self._pk_after_action(chan)

	def cmd_call(self, nick, chan):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return
			to_call = self.pk_current_bet - current.bet
			if to_call <= 0:
				self.sendmsg(chan, f'{color("ERROR", red)} Nothing to call. Use {bold}!check{bold}.')
				return

			available = self.get_chips(current.nick) - current.total_bet
			if to_call >= available:
				to_call = available
				current.all_in = True

			current.total_bet += to_call
			current.bet       += to_call
			current.acted      = True

			pot = sum(p.total_bet for p in self.pk_players)
			if current.all_in:
				self.sendmsg(chan, f'{bold}{current.nick}{bold} calls all-in ${to_call} (Pot: ${pot:,})')
			else:
				self.sendmsg(chan, f'{bold}{current.nick}{bold} calls ${to_call} (Pot: ${pot:,})')
			self._pk_after_action(chan)

	def cmd_raise(self, nick, chan, args):
		with self.lock:
			if self.pk_state != 'playing':
				return
			current = self.pk_players[self.pk_current_idx]
			if nick.lower() != current.nick.lower():
				return
			if not args:
				self.sendmsg(chan, f'{color("ERROR", red)} Usage: {bold}!raise <total>{bold} (e.g. !raise {self.pk_current_bet + self.pk_min_raise})')
				return
			try:
				raise_to = int(args[0])
			except ValueError:
				self.sendmsg(chan, f'{color("ERROR", red)} Invalid amount.')
				return

			min_total = self.pk_current_bet + self.pk_min_raise
			if raise_to < min_total:
				self.sendmsg(chan, f'{color("ERROR", red)} Minimum raise is to ${min_total}.')
				return

			cost = raise_to - current.bet
			available = self.get_chips(current.nick) - current.total_bet
			if cost > available:
				self.sendmsg(chan, f'{color("ERROR", red)} Not enough chips. You can bet up to ${current.bet + available}. Use {bold}!allin{bold}.')
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

			pot = sum(p.total_bet for p in self.pk_players)
			self.sendmsg(chan, f'{bold}{current.nick}{bold} raises to ${raise_to} (Pot: ${pot:,})')
			self._pk_after_action(chan)

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

			pot = sum(p.total_bet for p in self.pk_players)
			self.sendmsg(chan, f'{bold}{current.nick}{bold} goes ALL-IN for ${available}! (Pot: ${pot:,})')
			self._pk_after_action(chan)

	# ──────────────────── Shared Commands ────────────────────

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
						self.sendmsg(chan, 'Table closed \u2014 no players remain.')

			elif self.pk_state == 'lobby':
				idx = next((i for i, p in enumerate(self.pk_players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.pk_players.pop(idx)
					self.sendmsg(chan, f'{nick} left the poker table. [{len(self.pk_players)}/{MAX_PLAYERS}]')
					if not self.pk_players:
						if self.pk_lobby_timer:
							self.pk_lobby_timer.cancel()
						self._pk_reset()
						self.sendmsg(chan, 'Poker table closed.')

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
		self.sendmsg(chan, f'{bold}{color(" TOP 10 ", white, green)}{bold}')
		for i, (name, chips) in enumerate(leaderboard[:10], 1):
			self.sendmsg(chan, f' {bold}#{i}{bold} {name} \u2014 ${chips:,}')

	def cmd_help(self, nick, chan):
		self.sendmsg(chan, f'{bold}{color(" COMMANDS ", white, green)}{bold}')
		self.sendmsg(chan, f' {bold}\u2500\u2500 Blackjack \u2500\u2500{bold}')
		self.sendmsg(chan, f' {bold}!blackjack [bet]{bold} \u2014 Start or join (default: ${DEFAULT_BET})')
		self.sendmsg(chan, f' {bold}!hit{bold} \u2014 Draw a card  |  {bold}!stand{bold} \u2014 Keep hand  |  {bold}!double{bold} \u2014 Double down')
		self.sendmsg(chan, f' {bold}\u2500\u2500 Poker \u2500\u2500{bold}')
		self.sendmsg(chan, f' {bold}!poker{bold} \u2014 Start or join a table (blinds ${SMALL_BLIND}/${BIG_BLIND})')
		self.sendmsg(chan, f' {bold}!check{bold} | {bold}!call{bold} | {bold}!raise <amt>{bold} | {bold}!fold{bold} | {bold}!allin{bold}')
		self.sendmsg(chan, f' {bold}\u2500\u2500 General \u2500\u2500{bold}')
		self.sendmsg(chan, f' {bold}!deal{bold} \u2014 Force deal  |  {bold}!leave{bold} \u2014 Leave lobby  |  {bold}!chips{bold} \u2014 Check/reset chips  |  {bold}!top{bold} \u2014 Leaderboard')

	# ──────────────────── Blackjack Logic ────────────────────

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

		self.sendmsg(chan, f'{bold}{color(" CARDS DEALT ", white, orange)}{bold}')
		self.show_cards(chan, f'{bold}[Dealer]{bold}', self.dealer_hand, hide_first=True)
		self.sendmsg(chan, ' ')

		for player in self.players:
			bj = ''
			if player.is_blackjack:
				player.status = 'blackjack'
				bj = f' {color("BLACKJACK!", green)}'
			self.show_cards(chan, f'{bold}[{player.nick}]{bold} ({color(str(player.total), light_blue)}){bj}', player.hand)
			self.sendmsg(chan, ' ')

		self.last_move = time.time()
		threading.Thread(target=self._move_timer, args=(chan,), daemon=True).start()
		self._advance(chan)

	def _advance(self, chan):
		while self.current_idx < len(self.players):
			if self.players[self.current_idx].status == 'playing':
				p = self.players[self.current_idx]
				self.last_move = time.time()
				if len(p.hand) == 2:
					self.sendmsg(chan, f"{p.nick}: your turn \u2014 {bold}!hit{bold}, {bold}!stand{bold}, or {bold}!double{bold}")
				else:
					self.sendmsg(chan, f"{p.nick}: your turn \u2014 {bold}!hit{bold} or {bold}!stand{bold}")
				return
			self.current_idx += 1
		self._dealer_turn(chan)

	def _next_player(self, chan):
		self.current_idx += 1
		self._advance(chan)

	def _dealer_turn(self, chan):
		all_busted = all(p.status == 'busted' for p in self.players)

		self.sendmsg(chan, f'{bold}{color(" DEALER REVEALS ", white, orange)}{bold}')
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

		self.sendmsg(chan, f'{bold}{color(" RESULTS ", white, green)}{bold}')

		for p in self.players:
			if p.status == 'busted':
				new_chips = self.add_chips(p.nick, -p.bet)
				self.sendmsg(chan, f' {color(sym_cross, red)} {bold}{p.nick}{bold} busted ({bold}-${p.bet:,}{bold}) {sym_arrow} ${new_chips:,}')
			elif p.status == 'blackjack':
				if dealer_bj:
					chips = self.get_chips(p.nick)
					self.sendmsg(chan, f' {color(sym_dash, yellow)} {bold}{p.nick}{bold} push \u2014 both blackjack {sym_arrow} ${chips:,}')
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

		self.sendmsg(chan, f'{bold}{color(" DEALING POKER ", white, orange)}{bold}')
		self.sendmsg(chan, f'{sb.nick} posts small blind (${sb_amt})')
		self.sendmsg(chan, f'{bb.nick} posts big blind (${bb_amt})')

		for _ in range(2):
			for p in self.pk_players:
				p.hand.append(self.shoe.draw())

		for p in self.pk_players:
			c1 = format_card(*p.hand[0])
			c2 = format_card(*p.hand[1])
			self.sendmsg(p.nick, f'Your hole cards: {c1} {c2}')

		self.sendmsg(chan, 'Hole cards dealt \u2014 check your PMs!')
		pot = sum(p.total_bet for p in self.pk_players)
		self.sendmsg(chan, f'Pot: {bold}${pot}{bold}')

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

	def _pk_prompt(self, chan):
		p = self.pk_players[self.pk_current_idx]
		pot = sum(pp.total_bet for pp in self.pk_players)
		to_call = self.pk_current_bet - p.bet
		self.pk_last_move = time.time()
		if to_call > 0:
			self.sendmsg(chan, f'Pot: {bold}${pot:,}{bold} | {p.nick}: {bold}!call{bold} ${to_call}, {bold}!raise{bold} <total>, {bold}!fold{bold}, or {bold}!allin{bold}')
		else:
			self.sendmsg(chan, f'Pot: {bold}${pot:,}{bold} | {p.nick}: {bold}!check{bold}, {bold}!raise{bold} <total>, or {bold}!fold{bold}')

	def _pk_after_action(self, chan):
		active = [p for p in self.pk_players if not p.folded]
		if len(active) == 1:
			self._pk_win_by_fold(chan, active[0])
			return

		if self._pk_round_complete():
			self._pk_next_street(chan)
			return

		n = len(self.pk_players)
		for i in range(1, n + 1):
			idx = (self.pk_current_idx + i) % n
			p = self.pk_players[idx]
			if not p.folded and not p.all_in and not p.acted:
				self.pk_current_idx = idx
				self._pk_prompt(chan)
				return

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
			self.sendmsg(chan, f'{bold}{color(" FLOP ", white, green)}{bold}')
			self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		elif self.pk_street == 'flop':
			self.pk_street = 'turn'
			self.pk_community.append(self.shoe.draw())
			self.sendmsg(chan, f'{bold}{color(" TURN ", white, green)}{bold}')
			self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		elif self.pk_street == 'turn':
			self.pk_street = 'river'
			self.pk_community.append(self.shoe.draw())
			self.sendmsg(chan, f'{bold}{color(" RIVER ", white, green)}{bold}')
			self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		elif self.pk_street == 'river':
			self._pk_showdown(chan)
			return

		if len(can_bet) <= 1 and len(active) > 1:
			if not self.pk_cards_shown:
				self.pk_cards_shown = True
				self.sendmsg(chan, f'{bold}Players are all-in \u2014 showing cards:{bold}')
				for p in active:
					self.sendmsg(chan, f' {bold}[{p.nick}]{bold} {format_card(*p.hand[0])} {format_card(*p.hand[1])}')
			time.sleep(2)
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
		self.sendmsg(chan, f'{bold}{color(" SHOWDOWN ", white, orange)}{bold}')
		self.show_cards(chan, f'{bold}[Board]{bold}', self.pk_community)
		self.sendmsg(chan, ' ')

		evals = {}
		for p in self.pk_players:
			if not p.folded:
				rank = poker_best_hand(p.hand + self.pk_community)
				evals[p.nick] = rank
				name = poker_hand_name(rank)
				self.sendmsg(chan, f' {bold}[{p.nick}]{bold} {format_card(*p.hand[0])} {format_card(*p.hand[1])} \u2014 {color(name, yellow)}')
			else:
				self.sendmsg(chan, f' {bold}[{p.nick}]{bold} {color("folded", grey)}')

		self.sendmsg(chan, ' ')

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

		self.sendmsg(chan, f'{bold}{color(" RESULTS ", white, green)}{bold}')
		for p in self.pk_players:
			won = winnings.get(p.nick, 0)
			net = won - p.total_bet
			new_chips = self.add_chips(p.nick, net)
			if p.folded:
				if p.total_bet > 0:
					self.sendmsg(chan, f' {color(sym_cross, red)} {bold}{p.nick}{bold} folded ({bold}-${p.total_bet:,}{bold}) {sym_arrow} ${new_chips:,}')
				else:
					self.sendmsg(chan, f' {color(sym_cross, red)} {bold}{p.nick}{bold} folded {sym_arrow} ${new_chips:,}')
			elif net > 0:
				name = poker_hand_name(evals.get(p.nick, (0,)))
				self.sendmsg(chan, f' {color(sym_star, green)} {bold}{p.nick}{bold} wins ${won:,} ({name}) ({bold}+${net:,}{bold}) {sym_arrow} ${new_chips:,}')
			elif net == 0:
				self.sendmsg(chan, f' {color(sym_dash, yellow)} {bold}{p.nick}{bold} breaks even {sym_arrow} ${new_chips:,}')
			else:
				self.sendmsg(chan, f' {color(sym_cross, red)} {bold}{p.nick}{bold} loses ({bold}-${abs(net):,}{bold}) {sym_arrow} ${new_chips:,}')

		if self.db:
			self.db.save()
		self._pk_reset()

	def _pk_win_by_fold(self, chan, winner):
		total_pot = sum(p.total_bet for p in self.pk_players)
		self.sendmsg(chan, f'{bold}{winner.nick}{bold} wins ${total_pot:,} \u2014 everyone else folded!')

		for p in self.pk_players:
			if p.nick == winner.nick:
				net = total_pot - p.total_bet
			else:
				net = -p.total_bet
			if net != 0:
				self.add_chips(p.nick, net)

		if self.db:
			self.db.save()
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
						self.sendmsg(chan, f'{color("TIMEOUT!", red)} {current.nick} ran out of time \u2014 hand folded!')
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
			# Blackjack
			if self.state == 'lobby':
				idx = next((i for i, p in enumerate(self.players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.players.pop(idx)
					self.sendmsg(chan, f'{nick} left the table. [{len(self.players)}/{MAX_PLAYERS}]')
					if not self.players:
						if self.lobby_timer:
							self.lobby_timer.cancel()
						self.state = 'idle'
						self.sendmsg(chan, 'Table closed \u2014 no players remain.')
			elif self.state == 'playing':
				for i, p in enumerate(self.players):
					if p.nick.lower() == nick.lower() and p.status == 'playing':
						p.status = 'busted'
						self.sendmsg(chan, f'{nick} left \u2014 hand forfeited.')
						if i == self.current_idx:
							self._next_player(chan)
						break

			# Poker
			if self.pk_state == 'lobby':
				idx = next((i for i, p in enumerate(self.pk_players) if p.nick.lower() == nick.lower()), None)
				if idx is not None:
					self.pk_players.pop(idx)
					self.sendmsg(chan, f'{nick} left the poker table. [{len(self.pk_players)}/{MAX_PLAYERS}]')
					if not self.pk_players:
						if self.pk_lobby_timer:
							self.pk_lobby_timer.cancel()
						self._pk_reset()
						self.sendmsg(chan, 'Poker table closed.')
			elif self.pk_state == 'playing':
				for i, p in enumerate(self.pk_players):
					if p.nick.lower() == nick.lower() and not p.folded:
						p.folded = True
						self.sendmsg(chan, f'{nick} left \u2014 poker hand folded.')
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
