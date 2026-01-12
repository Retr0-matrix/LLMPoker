import uvicorn
import asyncio
import time
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from treys import Card, Evaluator, Deck


from llm_agent import LLMPokerAgent


API_KEY = "sk-" 
BASE_URL = "https://api.siliconflow.cn/v1"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

BOT_POOL = [
    {
        "name": "Jack", 
        "avatar": "https://api.dicebear.com/7.x/avataaars/svg?seed=Jack&clothing=blazerAndShirt&accessories=sunglasses", 
        "style": "You are Jack, a Poker Shark. You play extremely AGGRESSIVE. You bully others with big raises. You trash talk and bluff frequently."
    },
    {
        "name": "Emma", 
        "avatar": "https://api.dicebear.com/7.x/avataaars/svg?seed=Emma&clothing=collarAndSweater&top=bob", 
        "style": "You are Emma, a Math Nerd. You ONLY play based on Pot Odds and EV. You are emotionless and never bluff."
    },
    {
        "name": "Bob",  
        "avatar": "https://api.dicebear.com/7.x/avataaars/svg?seed=Bob&facialHair=beardMajestic", 
        "style": "You are Bob, a Drunk Gambler. You play completely randomly. Sometimes you go All-in with 7-2 offsuit just for fun."
    }
]

class PokerGame:
    def __init__(self):
        self.deck = Deck()
        self.players = []
        self.community_cards = []
        self.pot = 0
        self.high_bet = 0
        self.current_idx = 0
        self.dealer_pos = 0 
        self.sb_amount = 10
        self.bb_amount = 20
        self.hand_active = False
        self.log = []
        self.winners = []
        self.long_term_memory = [] 
        self.interaction_event = None 
        
        self.last_ai_thought = {"name": "System", "reasoning": "Waiting for game start..."}
        
        self.evaluator = Evaluator()
        
        try:
            self.llm = LLMPokerAgent(api_key=API_KEY, base_url=BASE_URL, model=MODEL_NAME)
            print("✅ AI Agent Loaded")
        except:
            self.llm = None

        self.add_player("You", False, "Human", "https://api.dicebear.com/7.x/avataaars/svg?seed=You")
        for i in range(3):
            p = BOT_POOL[i % len(BOT_POOL)]
            self.add_player(p['name'], True, p['style'], p['avatar'])
        
        self.start_new_hand()

    def add_player(self, name, is_bot, strategy, avatar):
        p = {
            "name": name, "stack": 1000, "buy_in_total": 1000, "hand": [], "bet": 0,
            "is_bot": is_bot, "folded": False, "allin": False, "has_acted": False,
            "avatar": avatar, "last_action": "", "reasoning": "", "strategy": strategy, "role": "",
            "profit": 0
        }
        self.players.append(p)

    def rebuy_player(self, idx):
        if idx < len(self.players):
            p = self.players[idx]
            p['stack'] += 1000
            p['buy_in_total'] += 1000
            p['profit'] = p['stack'] - p['buy_in_total']
            self.log.append(f"💰 {p['name']} Rebought 🪙1000")

    def adjust_bots(self, change):
        if self.hand_active: return 
        current_bots = [p for p in self.players if p['is_bot']]
        if change > 0:
            if len(self.players) >= 6: return
            cfg = BOT_POOL[len(current_bots) % len(BOT_POOL)]
            name = f"{cfg['name']} {len(current_bots)+1}"
            self.add_player(name, True, cfg['style'], cfg['avatar'])
        elif change < 0:
            if len(current_bots) > 1:
                for i in range(len(self.players)-1, 0, -1):
                    if self.players[i]['is_bot']:
                        self.players.pop(i)
                        break
        self.dealer_pos = 0

    def handle_interaction(self, source_idx, target_idx, item):
        src = self.players[source_idx]['name']
        tgt = self.players[target_idx]['name']
        
        self.interaction_event = {
            "from": source_idx,
            "to": target_idx,
            "item": item,
            "timestamp": int(time.time())
        }
        
        msg = f"{src} sent {item} to {tgt}"
        if item == "🍅": msg = f"{src} threw a Tomato at {tgt}!"
        if item == "🍵": msg = f"{src} served Tea to {tgt}."
        if item == "💣": msg = f"{src} dropped a Bomb on {tgt}!"
        self.log.append(f"💬 {msg}")

    def start_new_hand(self):
        for p in self.players:
            if p['stack'] <= 0:
                p['stack'] = 1000
                p['buy_in_total'] += 1000
                self.log.append(f"🔄 {p['name']} Auto-Rebuy")
            p['profit'] = p['stack'] - p['buy_in_total']

        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.high_bet = 0
        self.hand_active = True
        self.log = ["--- New Hand ---"]
        self.winners = []
        
        self.dealer_pos = (self.dealer_pos + 1) % len(self.players)
        
        for p in self.players:
            p['hand'] = self.deck.draw(2)
            p['folded'] = False; p['allin'] = False; p['bet'] = 0; p['has_acted'] = False
            p['last_action'] = ""; p['reasoning'] = ""; p['role'] = ""

        n = len(self.players)
        sb_pos = (self.dealer_pos + 1) % n
        bb_pos = (self.dealer_pos + 2) % n
        
        self.players[self.dealer_pos]['role'] = "D"
        self.players[sb_pos]['role'] = "SB"
        self.players[bb_pos]['role'] = "BB"
        
        self.post_bet(sb_pos, self.sb_amount, "SB", is_blind=True) 
        self.post_bet(bb_pos, self.bb_amount, "BB", is_blind=True)
        
        self.current_idx = (bb_pos + 1) % n

        self.last_ai_thought = {"name": "System", "reasoning": "New hand started. Waiting for action..."}

    def post_bet(self, idx, amt, label, is_blind=False):
        p = self.players[idx]
        actual = min(p['stack'], amt)
        p['stack'] -= actual
        p['bet'] += actual
        self.pot += actual
        
        if p['stack'] == 0: p['allin'] = True
        if p['bet'] > self.high_bet: 
            self.high_bet = p['bet']
            for other in self.players:
                if not other['folded'] and not other['allin']: other['has_acted'] = False
        
        p['last_action'] = label
        if not is_blind: p['has_acted'] = True
        p['profit'] = p['stack'] - p['buy_in_total']
        
        if not is_blind:
            self.log.append(f"{p['name']} {label} 🪙{actual}")

    def next_turn(self):
        active = [p for p in self.players if not p['folded']]
        if len(active) == 1: self.resolve_winner(); return

        not_allin_active = [p for p in active if not p['allin']]
        if len(not_allin_active) <= 1:
            if not not_allin_active or not_allin_active[0]['bet'] >= self.high_bet:
                self.run_all_in_showdown(); return

        can_advance = False
        if not not_allin_active: can_advance = True
        else:
            if all(p['bet'] == self.high_bet and p['has_acted'] for p in not_allin_active):
                can_advance = True

        if can_advance: self.advance_stage(); return
        
        original = self.current_idx
        while True:
            self.current_idx = (self.current_idx + 1) % len(self.players)
            p = self.players[self.current_idx]
            if not p['folded'] and not p['allin']:
                # 不要在这里清空 reasoning，否则 UI 会闪烁
                break
            if self.current_idx == original: self.run_all_in_showdown(); return

    def run_all_in_showdown(self):
        self.log.append("⚡ ALL-IN SHOWDOWN!")
        while len(self.community_cards) < 5:
            if len(self.community_cards) == 0: self.community_cards = self.deck.draw(3)
            else: self.community_cards.extend(self.deck.draw(1))
        self.resolve_winner()

    def advance_stage(self):
        self.high_bet = 0
        for p in self.players: 
            p['bet'] = 0; p['has_acted'] = False; p['last_action'] = "" 
        
        if len(self.community_cards) == 0: self.community_cards = self.deck.draw(3)
        elif len(self.community_cards) == 3: self.community_cards.extend(self.deck.draw(1))
        elif len(self.community_cards) == 4: self.community_cards.extend(self.deck.draw(1))
        else: self.resolve_winner(); return
            
        self.current_idx = (self.dealer_pos + 1) % len(self.players)
        while self.players[self.current_idx]['folded'] or self.players[self.current_idx]['allin']:
            self.current_idx = (self.current_idx + 1) % len(self.players)

    def resolve_winner(self):
        self.hand_active = False
        candidates = [p for p in self.players if not p['folded']]
        if len(candidates) == 1: self.winners = [candidates[0]['name']]
        else:
            scores = []
            for p in candidates:
                try: scores.append((self.evaluator.evaluate(self.community_cards, p['hand']), p))
                except: scores.append((9999, p))
            scores.sort(key=lambda x: x[0])
            self.winners = [x[1]['name'] for x in scores if x[0] == scores[0][0]]
            
        prize = int(self.pot / len(self.winners))
        for p in self.players:
            if p['name'] in self.winners:
                p['stack'] += prize
                p['profit'] = p['stack'] - p['buy_in_total']
        
        self.log.append(f"🏆 {', '.join(self.winners)} wins 🪙{self.pot}")

    def run_analysis(self):
        if not self.llm: return
        try:
            human = self.players[0]
            hand_log = "\n".join(self.log)
            cards = str(human['hand']) if not human['folded'] else "Mucked"
            analysis = self.llm.analyze_hand(hand_log, str(self.winners), cards)
            self.long_term_memory.insert(0, f"Hand: {analysis}")
            if len(self.long_term_memory) > 5: self.long_term_memory.pop()
        except: pass

    def execute_move(self, action, amount=0):
        p = self.players[self.current_idx]
        to_call = self.high_bet - p['bet']
        
        if action == "CHECK":
            if to_call > 0: self.post_bet(self.current_idx, to_call, "Call")
            else:
                p['last_action'] = "Check"; p['has_acted'] = True
                self.log.append(f"👀 {p['name']} Checked")
        elif action == "CALL":
            amt = p['stack'] if p['stack'] <= to_call else to_call
            self.post_bet(self.current_idx, amt, "Call")
        elif action == "RAISE":
            if amount >= p['stack'] + p['bet']: self.post_bet(self.current_idx, p['stack'], "All-in")
            else:
                if amount < self.high_bet + self.bb_amount: amount = self.high_bet + self.bb_amount
                self.post_bet(self.current_idx, amount - p['bet'], "Raise")
        elif action == "FOLD":
            p['folded'] = True; p['last_action'] = "Fold"
            self.log.append(f"❌ {p['name']} Folded")

        self.next_turn()

    def get_state(self):
        def card_str(ints): return [Card.int_to_str(c[0] if isinstance(c, list) else c) for c in ints]
        min_raise = max(self.bb_amount * 2, self.high_bet + self.bb_amount)
        evt = self.interaction_event
        self.interaction_event = None 

        return {
            "pot": self.pot,
            "community_cards": card_str(self.community_cards),
            "players": [
                {
                    **p, 
                    "hand": card_str(p['hand']) if (not p['is_bot'] or not self.hand_active or p['name'] in self.winners) else [],
                    "is_active": (self.current_idx == self.players.index(p) and self.hand_active),
                    "is_thinking": (self.current_idx == self.players.index(p) and self.hand_active and p['is_bot'])
                }
                for p in self.players
            ],
            "current_idx": self.current_idx,
            "hand_active": self.hand_active,
            "high_bet": self.high_bet,
            "min_raise": min_raise,
            "log": self.log[-8:],
            "winners": self.winners,
            "memory": self.long_term_memory,
            "interaction_event": evt,
            "last_ai_thought": self.last_ai_thought 
        }


    def bot_step(self):
        curr = self.players[self.current_idx]
        if not self.hand_active or not curr['is_bot']: return False
        time.sleep(0.5)
        def card_str(ints): return [Card.int_to_str(c[0] if isinstance(c, list) else c) for c in ints]
        
        stage = "PREFLOP"
        if len(self.community_cards) == 3: stage = "FLOP"
        elif len(self.community_cards) == 4: stage = "TURN"
        elif len(self.community_cards) == 5: stage = "RIVER"

        game_state = {
            "name": curr['name'], 
            "hand": card_str(curr['hand']), 
            "board": card_str(self.community_cards),
            "pot": self.pot, 
            "stack": curr['stack'], 
            "to_call": self.high_bet - curr['bet'],
            "stage": stage, 
            "history": "\n".join(self.log[-5:])
        }
        
        try:
            if self.llm:
                decision = self.llm.get_decision(game_state, curr['strategy'])
                action = decision.get('action', 'FOLD')
                amount = decision.get('amount', 0)
                reasoning = decision.get('reasoning', '...')
                curr['reasoning'] = reasoning
                
     
                self.last_ai_thought = {
                    "name": curr['name'],
                    "reasoning": reasoning
                }
            else:
                action = "CHECK" if self.high_bet <= curr['bet'] else "FOLD"
                amount = 0
                curr['reasoning'] = "No Brain."

            if action == "RAISE":
                to_call = self.high_bet - curr['bet']
                min_r = max(20, amount)
                total = curr['bet'] + to_call + min_r
                self.execute_move("RAISE", total)
            else:
                self.execute_move(action)
        except Exception as e:
            print(f"❌ Bot Error: {e}")
            self.execute_move("FOLD")
        return True

app = FastAPI()
game = PokerGame()

class ActionReq(BaseModel): action: str; amount: int = 0
class BotChangeReq(BaseModel): change: int
class InteractReq(BaseModel): target_idx: int; item: str

@app.get("/")
async def read_root(): return FileResponse("index.html")

@app.get("/state")
async def get_state(): return game.get_state()

@app.post("/action")
async def do_action(req: ActionReq, bg: BackgroundTasks):
    game.execute_move(req.action, req.amount)
    if not game.hand_active and game.llm: bg.add_task(game.run_analysis)
    return game.get_state()

@app.post("/bot")
async def run_bot(bg: BackgroundTasks):
    acted = game.bot_step()
    if not game.hand_active and game.llm: bg.add_task(game.run_analysis)
    return {"acted": acted}

@app.post("/next")
async def next_hand(): game.start_new_hand(); return game.get_state()

@app.post("/rebuy")
async def rebuy(): game.rebuy_player(0); return game.get_state()

@app.post("/change_bots")
async def change_bots(req: BotChangeReq): game.adjust_bots(req.change); return game.get_state()

@app.post("/interact")
async def interact(req: InteractReq):
    game.handle_interaction(0, req.target_idx, req.item)
    return game.get_state()

if __name__ == "__main__":

    uvicorn.run(app, host="0.0.0.0", port=8000)
