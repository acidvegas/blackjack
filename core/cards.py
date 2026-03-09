#!/usr/bin/env python
# BlackJack IRC Bot - Developed by acidvegas in Python (https://acid.vegas/blackjack)
# cards.py - Card data, game logic, and hand evaluation

import random
from collections import Counter
from itertools import combinations

# --- Game Constants ---
NUM_DECKS        = 6
MAX_PLAYERS      = 7
DEFAULT_BET      = 100
MIN_BET          = 10
MAX_BET          = None
STARTING_CHIPS   = 1000
MOVE_TIMEOUT     = 300
LOBBY_TIMEOUT    = 30
DB_SYNC_INTERVAL = 600
RESET_COOLDOWN   = 86400
SMALL_BLIND      = 5
BIG_BLIND        = 10
HOUSE_STARTING   = 0

# --- Card Data ---
SUITS = {
	'hearts'   : ('♥',  True),
	'diamonds' : ('♦',  True),
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
FACEDOWN = ('░' * 7,) * 5

RANK_IDX = {'A':1, '2':2, '3':3, '4':4, '5':5, '6':6, '7':7, '8':8, '9':9, '10':10, 'J':11, 'Q':13, 'K':14}
SUIT_BASE = {
	'spades'  : 0x1F0A0,
	'hearts'  : 0x1F0B0,
	'diamonds': 0x1F0C0,
	'clubs'   : 0x1F0D0,
}
FACEDOWN_UNI = '\U0001F0A0'

def unicode_card(rank, suit):
	return chr(SUIT_BASE[suit] + RANK_IDX[rank])


# --- Blackjack Hand Value ---

def hand_value(cards):
	total = sum(RANK_VALUES[rank] for rank, _ in cards)
	aces  = sum(1 for rank, _ in cards if rank == 'A')
	while total > 21 and aces:
		total -= 10
		aces  -= 1
	return total


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
	def __init__(self, num_decks=6, penetration=0.75):
		self.num_decks   = num_decks
		self.penetration = penetration
		self.cards       = []
		self.cut_pos     = 0
		self.needs_shuffle = True
		self.shuffle()

	def shuffle(self):
		self.cards = [(rank, suit) for _ in range(self.num_decks) for suit in SUITS for rank in RANKS]
		random.shuffle(self.cards)
		self.cut_pos = int(len(self.cards) * self.penetration)
		self.needs_shuffle = False

	@property
	def past_cut(self):
		dealt = (self.num_decks * 52) - len(self.cards)
		return dealt >= self.cut_pos

	def draw(self):
		if not self.cards:
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
