import streamlit as st
import time
from treys import Card, Evaluator, Deck
from llm_agent import LLMPokerAgent


def inject_custom_css():
    st.markdown("""
    <style>
    .stApp { background-color: #0e1117; }
    .poker-table {
        background: radial-gradient(circle, #1a472a 0%, #0d2b1a 100%);
        border: 10px solid #3d2b1f; border-radius: 50px; padding: 30px; margin-bottom: 20px;
        box-shadow: inset 0 0 100px #000; text-align: center;
    }
    .player-box {
        background: rgba(0, 0, 0, 0.6); border: 1px solid #444; border-radius: 15px;
        padding: 15px; text-align: center; transition: all 0.3s ease; min-height: 220px;
    }
    .player-active { border: 2px solid #ff4b4b !important; box-shadow: 0 0 20px rgba(255, 75, 75, 0.4); transform: scale(1.02); }
    .poker-card {
        display: inline-block; width: 55px; height: 80px; background-color: white;
        border-radius: 8px; margin: 3px; text-align: center; font-family: 'Arial Black', sans-serif;
        box-shadow: 2px 2px 8px rgba(0,0,0,0.5);
    }
    .card-rank { font-size: 20px; font-weight: bold; margin-top: 5px; line-height: 1; color: black; }
    .card-suit { font-size: 28px; line-height: 1; }
    .status-badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: bold; text-transform: uppercase; }
    .badge-dealer { background-color: #f1c40f; color: black; }
    .badge-allin { background-color: #e74c3c; color: white; }
    
    .profit-plus { color: #2ecc71; font-weight: bold; font-size: 0.9em; }
    .profit-minus { color: #e74c3c; font-weight: bold; font-size: 0.9em; }
    
    .busted-screen {
        background-color: #2c0b0e; border: 2px solid #e74c3c; border-radius: 20px;
        padding: 40px; text-align: center; margin-top: 50px;
    }
    </style>
    """, unsafe_allow_html=True)


class Player:
    def __init__(self, name, stack=1000, is_bot=True):
        self.name = name
        self.stack = stack
        self.buy_in_total = stack
        self.hand = []
        self.is_folded = False
        self.is_allin = False
        self.current_bet = 0
        self.is_bot = is_bot
        self.last_action = ""
        self.reasoning = ""
        self.role = ""
        self.has_acted = False

class PokerGame:
    def __init__(self):
        self.evaluator = Evaluator()
        self.players = [Player("Human", is_bot=False)] + [Player(f"Bot {i+1}") for i in range(3)]
        self.llm = LLMPokerAgent(
            api_key="sk-bsuespgnaotswlttledrfzcxgirnpwsjafthsfgqqmgwemro",
            base_url="https://api.siliconflow.cn/v1",
            model="deepseek-ai/DeepSeek-V3"
        )
        self.community_cards = []
        self.pot = 0
        self.stage = "PREFLOP"
        self.dealer_pos = 0
        self.current_idx = 0
        self.high_bet = 0
        self.log = []
        self.hand_active = False
        self.winners = []
        self.long_term_memory = []

    def update_bot_count(self, num_bots):
        current_bots = len(self.players) - 1
        if num_bots > current_bots:
            for i in range(current_bots, num_bots):
                self.players.append(Player(f"Bot {i+1}"))
        elif num_bots < current_bots:
            self.players = self.players[:num_bots+1]
        self.dealer_pos = 0
        self.start_new_hand()

    def start_new_hand(self):
        self.log = [] 
        
        for p in self.players:
            if p.is_bot and p.stack <= 0:
                p.stack = 1000
                p.buy_in_total += 1000
                self.log.append(f"💰 {p.name} rebought $1000 (Auto).")
        
        human = self.players[0]
        if human.stack <= 0:
            self.stage = "GAME OVER"
            self.hand_active = False
            return 

        self.deck = Deck()
        self.community_cards = []
        self.pot = self.high_bet = 0
        self.stage = "PREFLOP"
        self.winners = []
        self.hand_active = True
        
        for p in self.players:
            p.hand = self.deck.draw(2)
            p.is_folded = p.is_allin = p.has_acted = False
            p.current_bet = 0
            p.last_action = p.reasoning = p.role = ""

        self.log.append("--- New Hand Started ---")

        total = len(self.players)
        self.dealer_pos = (self.dealer_pos + 1) % total
        self.players[self.dealer_pos].role = "Dealer"
        
        sb_pos = (self.dealer_pos + 1) % total
        bb_pos = (self.dealer_pos + 2) % total
        
        self.post_bet(sb_pos, 10, "Post SB")
        self.post_bet(bb_pos, 20, "Post BB")
        self.current_idx = (bb_pos + 1) % total

    def post_bet(self, idx, amount, label=""):
        p = self.players[idx]
        actual = min(p.stack, amount)
        p.stack -= actual
        p.current_bet += actual
        self.pot += actual
        if p.stack <= 0: p.is_allin = True
        if p.current_bet > self.high_bet:
            self.high_bet = p.current_bet
            for other in self.players:
                if other != p and not other.is_folded and not other.is_allin:
                    other.has_acted = False
        if label: p.last_action = f"{label} ${p.current_bet}"

    def execute_move(self, action, amount=0):
        p = self.players[self.current_idx]
        to_call = self.high_bet - p.current_bet
        if to_call > 0 and action == "CHECK": action = "FOLD"

        if action == "FOLD":
            p.is_folded = True
            p.last_action = "FOLD"
            self.log.append(f"❌ {p.name} folds.")
        elif action == "RAISE":
            self.post_bet(self.current_idx, to_call + amount, "Raise")
            p.has_acted = True
            self.log.append(f"🔥 {p.name} raises to ${p.current_bet}!")
        elif action == "CALL":
            self.post_bet(self.current_idx, to_call, "Call")
            p.has_acted = True
            self.log.append(f"✅ {p.name} calls ${to_call}")
        elif action == "CHECK":
            p.has_acted = True
            p.last_action = "CHECK"
            self.log.append(f"😴 {p.name} checks.")

        if len([pl for pl in self.players if not pl.is_folded]) <= 1:
            self.stage = "SHOWDOWN"; self.resolve_winners()
            return

        if self.is_round_over(): self.advance_stage()
        else: self.move_to_next()

    def is_round_over(self):
        active_deciders = [p for p in self.players if not p.is_folded and not p.is_allin]
        if not active_deciders: return True
        return all(p.current_bet == self.high_bet for p in active_deciders) and all(p.has_acted for p in active_deciders)

    def move_to_next(self):
        total = len(self.players)
        for _ in range(total):
            self.current_idx = (self.current_idx + 1) % total
            p = self.players[self.current_idx]
            if not p.is_folded and not p.is_allin: return
        self.advance_stage()

    def advance_stage(self):
        for p in self.players:
            p.current_bet = 0
            p.has_acted = False
            if not p.is_folded: p.last_action = ""
        self.high_bet = 0
        total = len(self.players)

        if self.stage == "PREFLOP":
            self.stage = "FLOP"; self.community_cards = self.deck.draw(3)
        elif self.stage == "FLOP":
            self.stage = "TURN"; self.community_cards.extend(self.deck.draw(1))
        elif self.stage == "TURN":
            self.stage = "RIVER"; self.community_cards.extend(self.deck.draw(1))
        else:
            self.resolve_winners(); return
        
        self.log.append(f"--- Dealing {self.stage} ---")
        active_deciders = [p for p in self.players if not p.is_folded and not p.is_allin]
        if len(active_deciders) > 1:
            self.current_idx = (self.dealer_pos + 1) % total
            while self.players[self.current_idx].is_folded or self.players[self.current_idx].is_allin:
                self.current_idx = (self.current_idx + 1) % total

    def summarize_hand_with_ai(self):
        human = self.players[0]
        if human.is_folded and self.stage == "PREFLOP":
            return

        hand_log = "\n".join(self.log)
        winner_info = ", ".join(self.winners) if self.winners else "None"
        human_cards = "Unknown (Mucked)"
        if self.stage == "SHOWDOWN" and not human.is_folded:
            human_cards = Card.print_pretty_cards(human.hand)
        
        analysis = self.llm.analyze_hand(hand_log, winner_info, human_cards)
        hand_id = len(self.long_term_memory) + 1
        self.long_term_memory.append(f"Hand #{hand_id}: {analysis}")
        
        if len(self.long_term_memory) > 6: 
            self.long_term_memory.pop(0)

    def resolve_winners(self):
        self.hand_active = False
        candidates = [p for p in self.players if not p.is_folded]
        if len(candidates) == 1:
            winner = candidates[0]
            winner.stack += self.pot
            self.winners = [winner.name]
            self.log.append(f"🏆 {winner.name} wins ${self.pot}")
        else:
            scores = [(self.evaluator.evaluate(self.community_cards, p.hand), p) for p in candidates]
            scores.sort(key=lambda x: x[0])
            best = scores[0][0]
            ws = [x[1] for x in scores if x[0] == best]
            for w in ws: w.stack += self.pot // len(ws)
            self.winners = [w.name for w in ws]
            self.log.append(f"🏆 Winners: {self.winners}")
        
        self.dealer_pos = (self.dealer_pos + 1) % len(self.players)

# ==========================================
# 2. UI
# ==========================================
def get_card_html(card_int, hidden=False):
    if hidden: return '<div class="poker-card" style="background:linear-gradient(135deg,#2c3e50 25%,#34495e 100%);border:2px solid #ecf0f1;"><div style="height:100%;width:100%;display:flex;align-items:center;justify-content:center;color:white;font-size:20px;">♠️</div></div>'
    if card_int is None: return ""
    if isinstance(card_int, list): card_int = card_int[0]
    try:
        c_str = Card.int_to_str(card_int)
        rank, suit = c_str[0].replace('T','10'), c_str[1]
        suit_map = {'s': ('♠', '#2c3e50'), 'h': ('♥', '#e74c3c'), 'd': ('♦', '#e74c3c'), 'c': ('♣', '#2c3e50')}
        sym, color = suit_map.get(suit.lower(), ('?', 'black'))
        return f'<div class="poker-card"><div class="card-rank" style="color:{color}">{rank}</div><div class="card-suit" style="color:{color}">{sym}</div></div>'
    except: return ""

def render_cards(cards, hidden=False):
    html = "".join([get_card_html(c, hidden) for c in (cards if not hidden else [0,0])])
    st.markdown(f"<div style='display:flex;justify-content:center;'>{html}</div>", unsafe_allow_html=True)


st.set_page_config(page_title="DeepSeek Poker Pro", layout="wide")
inject_custom_css()

if 'game' not in st.session_state:
    st.session_state.game = PokerGame()
    st.session_state.game.start_new_hand()

game = st.session_state.game

with st.sidebar:
    st.title("🎲 Pro Poker Table")
    
    current_bot_count = len(game.players) - 1
    num_bots = st.slider("Number of AI Opponents", 1, 5, current_bot_count)
    if num_bots != current_bot_count:
        game.update_bot_count(num_bots)
        st.rerun()
    
    st.divider()
    
    is_broke = (game.stage == "GAME OVER") or (game.players[0].stack <= 0 and not game.hand_active)
    if is_broke:
        st.error("💸 You are Broke!")
        if st.button("🔄 Rebuy ($1000)", type="primary", use_container_width=True):
            game.players[0].stack = 1000
            game.players[0].buy_in_total += 1000 
            game.start_new_hand()
            st.rerun()
    else:
        user_instr = st.text_area("AI Personality", "Play Loose-Aggressive. Bluff if you sense weakness.")
        if st.button("New Hand / Reset", use_container_width=True):
            game.start_new_hand(); st.rerun()
            
    st.divider()
    with st.expander("🧠 AI Analysis (Learned)", expanded=True):
        if not game.long_term_memory:
            st.caption("No patterns analyzed yet.")
        else:
            for mem in game.long_term_memory:
                st.info(mem) 

    st.divider()
    for msg in reversed(game.log): st.caption(msg)

if game.stage == "GAME OVER":
    st.markdown("""<div class="busted-screen"><h1>BUSTED!</h1><p>Please check the sidebar to Rebuy.</p></div>""", unsafe_allow_html=True)
    st.stop() 

st.markdown('<div class="poker-table">', unsafe_allow_html=True)
st.markdown(f"<h3 style='text-align:center;color:white;'>{game.stage} | Pot: ${game.pot}</h3>", unsafe_allow_html=True)
if game.community_cards: render_cards(game.community_cards)
else: st.markdown("<div style='height:80px;text-align:center;color:#ffffff66;'>Waiting for Flop...</div>", unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

cols = st.columns(len(game.players))
active_deciders = [p for p in game.players if not p.is_folded and not p.is_allin]

for i, p in enumerate(game.players):
    with cols[i]:
        is_turn = (game.current_idx == i and game.hand_active and not p.is_folded and not p.is_allin)
        p_name = "👤 You" if p.name == "Human" else f"🤖 {p.name}"
        
        profit = p.stack - p.buy_in_total
        profit_class = "profit-plus" if profit >= 0 else "profit-minus"
        profit_str = f"+${profit}" if profit >= 0 else f"-${abs(profit)}"
        
        st.markdown(f"""
<div class="player-box {'player-active' if is_turn else ''}">
<div style="color:white;font-weight:bold;font-size:1.1em;">{p_name}</div>
<div style="color:#2ecc71;font-size:1.2em;font-weight:bold;">${p.stack}</div>
<div class="{profit_class}">{profit_str}</div>
<div style="color:#e67e22;font-size:0.9em;">Bet: ${p.current_bet}</div>
<div style="margin-top:5px;">
{"<span class='status-badge badge-dealer'>Dealer</span>" if "Dealer" in p.role else ""}
{"<span class='status-badge badge-allin'>ALL-IN</span>" if p.is_allin else ""}
</div></div>
""", unsafe_allow_html=True)
        
        if p.is_folded: st.markdown("<div style='text-align:center;color:#666;padding:20px;'>FOLDED</div>", unsafe_allow_html=True)
        else: render_cards(p.hand, hidden=(p.is_bot and game.hand_active))
        if p.last_action: st.info(p.last_action)
        if p.reasoning: 
            with st.expander("💭 Reasoning"): st.markdown(f"<small>{p.reasoning}</small>", unsafe_allow_html=True)

st.divider()

if not game.hand_active:
    st.markdown(f"<h2 style='text-align:center;color:white;'>Winner: {', '.join(game.winners)}</h2>", unsafe_allow_html=True)
    if st.button("Continue (Analyze & Next Hand)", type="primary", use_container_width=True):
        with st.spinner("🧠 AI is analyzing your gameplay..."):
            game.summarize_hand_with_ai()
        game.start_new_hand()
        st.rerun()
elif len(active_deciders) >= 2 or (len(active_deciders) == 1 and game.high_bet > active_deciders[0].current_bet):
    curr_p = game.players[game.current_idx]
    if curr_p.is_bot:
        with st.spinner(f"{curr_p.name} thinking..."):
            to_call = game.high_bet - curr_p.current_bet
            memory_context = "\n".join(game.long_term_memory)
            state = {
                "name": curr_p.name,
                "hand": [Card.int_to_str(c) for c in curr_p.hand],
                "board": [Card.int_to_str(c) for c in game.community_cards],
                "pot": game.pot,
                "stack": curr_p.stack,
                "to_call": to_call,
                "stage": game.stage,
                "history": "\n".join(game.log[-12:])
            }
            decision = game.llm.get_decision(state, user_instr, memory=memory_context)
            curr_p.reasoning = decision.get('reasoning', '')
            game.execute_move(decision['action'], decision.get('amount', 0))
            st.rerun()
    else:
        st.markdown("<h3 style='color:white;'>Your Turn</h3>", unsafe_allow_html=True)
        to_call = game.high_bet - curr_p.current_bet
        c1, c2, c3 = st.columns([1,1,2])
        with c1:
            label = "Check" if to_call == 0 else f"Call ${to_call}"
            if st.button(label, use_container_width=True):
                game.execute_move("CHECK" if to_call == 0 else "CALL"); st.rerun()
        with c2:
            if st.button("Fold", use_container_width=True): game.execute_move("FOLD"); st.rerun()
        with c3:
            if curr_p.stack > 0:
                min_raise = 20
                if to_call > 0: min_raise = to_call * 2
                slider_min = min(int(curr_p.stack), min_raise)
                slider_max = int(curr_p.stack)
                if slider_min >= slider_max: slider_min = slider_max
                if slider_max > 0:
                    amt = st.slider("Raise", slider_min, slider_max, slider_min)
                    if st.button("Confirm", type="primary", use_container_width=True):
                        game.execute_move("RAISE", amt); st.rerun()
                else:
                    st.info(f"Only option: All-in ${slider_max}")
                    if st.button(f"All-in ${slider_max}", type="primary", use_container_width=True):
                        game.execute_move("RAISE", slider_max); st.rerun()
else:
    st.info("No more bets possible.")
    if st.button(f"Deal {game.stage} ➡️", type="primary", use_container_width=True):
        game.advance_stage(); st.rerun()