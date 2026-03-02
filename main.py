import uvicorn
import time
import logging
import asyncio
import datetime 
import json     
import os
import numpy as np
from collections import defaultdict
from contextlib import asynccontextmanager

import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from treys import Card, Evaluator, Deck


from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score, accuracy_score

from llm_agent import LLMPokerAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

API_KEY = "sk-ngqawrtpwkmmosihwmqfirmovzbxtajbivsdzisyppsufjxn" 
BASE_URL = "https://api.siliconflow.cn/v1"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

BOT_POOL = [
    {"name": "Jack", "avatar": "https://api.dicebear.com/7.x/avataaars/svg?seed=Jack", "style": "TOXIC AGGRESSIVE. If Human insults you, GO ALL-IN. Play aggressively."},
    {"name": "Emma", "avatar": "https://api.dicebear.com/7.x/avataaars/svg?seed=Emma", "style": "Cold Logic. Only play premium hands. Fold if unsure."},
    {"name": "Bob", "avatar": "https://api.dicebear.com/7.x/avataaars/svg?seed=Bob", "style": "Loose Passive. You love to CALL to see the flop. But if someone raises a huge amount, you will FOLD unless you have a pair."}
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
        self.hand_count = 0
        
        self.user_wallet = 5000  
        self.current_bets = {}   
        self.history = []        
        
        self.evaluator = Evaluator()
        try:
            self.llm = LLMPokerAgent(api_key=API_KEY, base_url=BASE_URL, model=MODEL_NAME)
            logger.info("✅ AI Agent Loaded")
        except Exception as e:
            self.llm = None
            logger.error(f"❌ AI Agent Load Failed: {e}")

        self.add_player("You", False, "Human", "https://api.dicebear.com/7.x/avataaars/svg?seed=You")
        for i in range(3):
            p = BOT_POOL[i % len(BOT_POOL)]
            self.add_player(p['name'], True, p['style'], p['avatar'])
        
        self.start_new_hand()

    def add_player(self, name, is_bot, strategy, avatar):
        p = {
            "name": name, "stack": 1000, "buy_in_total": 1000, "hand": [], "bet": 0,
            "invested": 0, 
            "is_bot": is_bot, "folded": False, "allin": False, "has_acted": False,
            "avatar": avatar, 
            "strategy": strategy, 
            "base_persona": strategy,
            "role": "", 
            "profit": 0,
            "hands_played": 0, "hands_won": 0,
            "history_log": [],
            "current_metrics": {},
            "saw_flop_this_hand": False,
            "ml_data": {
                "y_true_action": [], "y_pred_action": [],
                "y_true_amount": [], "y_pred_amount": []
            }
        }
        self.players.append(p)

    def is_good_preflop(self, hand):
        try:
            r1 = Card.get_rank_int(hand[0])
            r2 = Card.get_rank_int(hand[1])
            s1 = Card.get_suit_int(hand[0])
            s2 = Card.get_suit_int(hand[1])
            high, low = max(r1, r2), min(r1, r2)
            is_suited = (s1 == s2)
            if high == low and high >= 5: return True
            if high >= 10 and low >= 9: return True
            if is_suited and (high - low == 1) and high >= 6: return True
            return False
        except: return False


    def get_ground_truth(self, p, to_call):
        pot = self.pot
        board_len = len(self.community_cards)
        

        if board_len == 0:
            try:
                r1 = Card.get_rank_int(p['hand'][0])
                r2 = Card.get_rank_int(p['hand'][1])
                s1 = Card.get_suit_int(p['hand'][0])
                s2 = Card.get_suit_int(p['hand'][1])
                high, low = max(r1, r2), min(r1, r2)
                is_suited = (s1 == s2)
                
                if (high == low and high >= 10) or (high == 12 and low == 11):
                    return {"action": "RAISE", "amount": to_call + self.bb_amount * 3}

                elif (high == low and high >= 6) or (high == 12 and low >= 9 and is_suited) or (high == 11 and low == 10 and is_suited):
                    return {"action": "CALL", "amount": to_call}

                else:
                    return {"action": "CHECK" if to_call == 0 else "FOLD", "amount": 0}
            except:
                return {"action": "CHECK" if to_call == 0 else "FOLD", "amount": 0}

 
        else:
            try:
                score = self.evaluator.evaluate(self.community_cards, p['hand'])
                equity_percentile = 1.0 - (score / 7462.0)
                
                if equity_percentile >= 0.80:   
                    return {"action": "RAISE", "amount": to_call + int(pot * 0.5)}
                elif equity_percentile >= 0.40: 
                    return {"action": "CALL", "amount": to_call}
                else:                    
                    return {"action": "CHECK" if to_call == 0 else "FOLD", "amount": 0}
            except:
                return {"action": "CHECK" if to_call == 0 else "FOLD", "amount": 0}

    def start_new_hand(self):
        if self.hand_count > 0 and self.hand_count % 10 == 0:
            self.trigger_ai_reflection()

        self.hand_count += 1
        for p in self.players:
            if p['stack'] <= 0:
                p['stack'] = 1000
                p['buy_in_total'] += 1000
                self.log.append(f"🔄 {p['name']} Rebought")
            p['profit'] = p['stack'] - p['buy_in_total']
            p['saw_flop_this_hand'] = False
            p['invested'] = 0 
            
            p['current_metrics'] = {
                'preflop_dec': 0, 'preflop_cor': 0,
                'saw_flop': False, 'wtsd': False, 'won': False,
                'vpip': False, 'pfr': False
            }

        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.high_bet = 0
        self.hand_active = True
        self.log.append(f"--- Hand #{self.hand_count} ---")
        self.winners = []
        self.current_bets = {}
        
        self.dealer_pos = (self.dealer_pos + 1) % len(self.players)
        for p in self.players:
            p['hand'] = self.deck.draw(2)
            p['folded'] = False; p['allin'] = False; p['bet'] = 0; p['has_acted'] = False
            p['role'] = ""

        n = len(self.players)
        sb_pos = (self.dealer_pos + 1) % n
        bb_pos = (self.dealer_pos + 2) % n
        
        self.players[self.dealer_pos]['role'] = "D"
        self.players[sb_pos]['role'] = "SB"
        self.players[bb_pos]['role'] = "BB"
        
        self.post_bet(sb_pos, self.sb_amount, "SB", is_blind=True) 
        self.post_bet(bb_pos, self.bb_amount, "BB", is_blind=True)
        self.current_idx = (bb_pos + 1) % n
        
    def trigger_ai_reflection(self):
        if not self.llm: return
        self.log.append("🧠 AI Agents are analyzing their mistakes...")
        logger.info(f"--- Triggering Reflection at Hand {self.hand_count} ---")
        for p in self.players:
            if p['is_bot']:
                v_hands = sum(1 for h in p['history_log'] if h.get('vpip'))
                tot = max(1, len(p['history_log']))
                vpip = round((v_hands / tot) * 100, 1)
                
                reflection_msg = f"[SYSTEM] Past 20 hands: Profit ${p['profit']}, VPIP {vpip}%. If negative profit, you are losing. If VPIP > 40%, you play too loose. \nReflect on your mistakes. Output ONLY 1 short sentence describing your NEW specific poker adjustment."
                
                try:
                    new_strat = self.llm.chat(p['name'], p['strategy'], reflection_msg, "")
                    if new_strat and len(new_strat) > 5:
                        p['strategy'] = f"{p['base_persona']} \n\n[CRITICAL LESSON LEARNED]: {new_strat}"
                        self.log.append(f"💡 {p['name']} learned: {new_strat[:60]}...")
                except Exception as e: pass

    def post_bet(self, idx, amt, label, is_blind=False):
        p = self.players[idx]
        actual = min(p['stack'], amt)
        p['stack'] -= actual
        p['bet'] += actual
        p['invested'] += actual
        self.pot += actual
        
        if p['stack'] == 0: p['allin'] = True
        if p['bet'] > self.high_bet: 
            self.high_bet = p['bet']
            for other in self.players:
                if not other['folded'] and not other['allin']: other['has_acted'] = False
        
        if not is_blind: 
            p['has_acted'] = True
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
            if not p['folded'] and not p['allin']: break
            if self.current_idx == original: self.run_all_in_showdown(); return

    def run_all_in_showdown(self):
        self.log.append("⚡ ALL-IN SHOWDOWN!")
        pre_count = len(self.community_cards)
        while len(self.community_cards) < 5:
            if len(self.community_cards) == 0: self.community_cards = self.deck.draw(3)
            else: self.community_cards.extend(self.deck.draw(1))
            
        if pre_count == 0:
            for p in self.players:
                if not p['folded']: p['saw_flop_this_hand'] = True
                
        self.resolve_winner()

    def advance_stage(self):
        self.high_bet = 0
        for p in self.players: p['bet'] = 0; p['has_acted'] = False
        
        pre_count = len(self.community_cards)
        
        if len(self.community_cards) == 0: self.community_cards = self.deck.draw(3)
        elif len(self.community_cards) == 3: self.community_cards.extend(self.deck.draw(1))
        elif len(self.community_cards) == 4: self.community_cards.extend(self.deck.draw(1))
        else: self.resolve_winner(); return
        
        if pre_count == 0 and len(self.community_cards) == 3:
            for p in self.players:
                if not p['folded']: p['saw_flop_this_hand'] = True
                
        self.current_idx = (self.dealer_pos + 1) % len(self.players)
        while self.players[self.current_idx]['folded'] or self.players[self.current_idx]['allin']:
            self.current_idx = (self.current_idx + 1) % len(self.players)

    def resolve_winner(self):
        self.hand_active = False
        candidates = [p for p in self.players if not p['folded']]
        is_showdown = len(candidates) > 1 
        
        investments = sorted(list(set(p['invested'] for p in self.players if p['invested'] > 0)))
        pots = []
        prev_inv = 0
        for inv in investments:
            tier_amt = inv - prev_inv
            tier_pot = 0
            eligible = []
            for p in self.players:
                if p['invested'] >= inv:
                    tier_pot += tier_amt
                    if not p['folded']: eligible.append(p)
            pots.append({"amount": tier_pot, "eligible": eligible})
            prev_inv = inv
            
        self.winners = []
        for pot in pots:
            if not pot['eligible']: continue
            if len(pot['eligible']) == 1: winners = pot['eligible']
            else:
                scores = []
                for p in pot['eligible']:
                    try: scores.append((self.evaluator.evaluate(self.community_cards, p['hand']), p))
                    except: scores.append((9999, p))
                min_score = min(s[0] for s in scores)
                winners = [s[1] for s in scores if s[0] == min_score]
            prize = pot['amount'] // len(winners)
            for w in winners:
                w['stack'] += prize
                if w['name'] not in self.winners: self.winners.append(w['name'])

        hand_snapshot = {}
        for p in self.players:
            p['hands_played'] += 1
            if p['name'] in self.winners: p['hands_won'] += 1
            p['profit'] = p['stack'] - p['buy_in_total']

            m = p['current_metrics']
            m['won'] = p['name'] in self.winners
            m['saw_flop'] = p['saw_flop_this_hand']
            m['wtsd'] = is_showdown and not p['folded']
            
            p['history_log'].append(m)
            if len(p['history_log']) > 20: p['history_log'].pop(0)
                
            tot_win_hands = len(p['history_log'])
            if tot_win_hands == 0: continue
            
            vpip_c = sum(1 for h in p['history_log'] if h['vpip'])
            pfr_c = sum(1 for h in p['history_log'] if h['pfr'])
            pf_dec = sum(h['preflop_dec'] for h in p['history_log'])
            pf_cor = sum(h['preflop_cor'] for h in p['history_log'])
            
            tot_wsf = sum(1 for h in p['history_log'] if h['saw_flop'])
            w_wsf = sum(1 for h in p['history_log'] if h['saw_flop'] and h['won'])
            tot_wtsd = sum(1 for h in p['history_log'] if h['wtsd'])
            w_wtsd = sum(1 for h in p['history_log'] if h['wtsd'] and h['won'])
            
            hp = max(1, p['hands_played'])

            ml_acc = 0.0; ml_mse = 0.0; ml_mae = 0.0; ml_r2 = 0.0
            y_t_a = p['ml_data']['y_true_action']
            y_p_a = p['ml_data']['y_pred_action']
            y_t_v = p['ml_data']['y_true_amount']
            y_p_v = p['ml_data']['y_pred_amount']
            
            if len(y_t_a) > 0:
                ml_acc = accuracy_score(y_t_a, y_p_a) * 100
                if len(y_t_v) > 0:
                    ml_mse = mean_squared_error(y_t_v, y_p_v)
                    ml_mae = mean_absolute_error(y_t_v, y_p_v)
                    if len(y_t_v) > 1 and np.var(y_t_v) > 0:
                        raw_r2 = r2_score(y_t_v, y_p_v)
                        ml_r2 = max(0.0, raw_r2)

            hand_snapshot[p['name']] = {
                "profit": p['profit'], 
                "win_rate": round((p['hands_won'] / hp) * 100, 2),
                "bb_100": round((p['profit'] / self.bb_amount) * (100 / hp), 2),
                "vpip": round((vpip_c / tot_win_hands) * 100, 1),
                "pfr": round((pfr_c / tot_win_hands) * 100, 1),
                "preflop_acc": round((pf_cor / pf_dec) * 100, 1) if pf_dec > 0 else 0,
                "w_sd": round((w_wtsd / tot_wtsd) * 100, 1) if tot_wtsd > 0 else 0,
                "wwsf": round((w_wsf / tot_wsf) * 100, 1) if tot_wsf > 0 else 0,
                "ml_accuracy": round(ml_acc, 2),
                "ml_r2": round(ml_r2, 4),
                "ml_mse": round(ml_mse, 2),
                "ml_mae": round(ml_mae, 2)
            }
            
        self.log.append(f"🏆 {', '.join(self.winners)} wins!")
        self.history.append({"id": self.hand_count, "data": hand_snapshot})
        if len(self.history) > 2000: self.history.pop(0)
        if len(self.log) > 200: self.log = self.log[-200:]

    def execute_move(self, action, amount=0):
        p = self.players[self.current_idx]
        to_call = self.high_bet - p['bet']
        is_preflop = len(self.community_cards) == 0
        
        if action in ["CALL", "RAISE"]: p['current_metrics']['vpip'] = True
        if action == "RAISE": p['current_metrics']['pfr'] = True

        if is_preflop and action != "CHECK" and p['current_metrics']['preflop_dec'] == 0:
            p['current_metrics']['preflop_dec'] = 1
            good_hand = self.is_good_preflop(p['hand'])
            if action in ["CALL", "RAISE"]: p['current_metrics']['preflop_cor'] = 1 if good_hand else 0
            elif action == "FOLD": p['current_metrics']['preflop_cor'] = 1 if not good_hand else 0

        if action == "CHECK":
            if to_call > 0: self.post_bet(self.current_idx, to_call, "Call")
            else: self.post_bet(self.current_idx, 0, "Check")
        elif action == "CALL": self.post_bet(self.current_idx, min(p['stack'], to_call), "Call")
        elif action == "RAISE": self.post_bet(self.current_idx, amount - p['bet'], "Raise")
        elif action == "FOLD": p['folded'] = True; self.log.append(f"❌ {p['name']} Folded")
        self.next_turn()

    def _get_card_str(self, ints):
        return [Card.int_to_str(c[0] if isinstance(c, list) else c) for c in ints]

    def get_state(self):
        min_raise = max(self.bb_amount * 2, self.high_bet + self.bb_amount)
        return {
            "pot": self.pot, "community_cards": self._get_card_str(self.community_cards),
            "players": [{**p, "hand": self._get_card_str(p['hand']) if (not p['is_bot'] or not self.hand_active or p['name'] in self.winners) else []} for p in self.players],
            "current_idx": self.current_idx, "hand_active": self.hand_active,
            "high_bet": self.high_bet, "min_raise": min_raise,
            "log": self.log[-8:], "winners": self.winners, "user_wallet": self.user_wallet
        }

    def bot_step(self):
        curr = self.players[self.current_idx]
        if not self.hand_active or not curr['is_bot']: return False
        
        to_call = self.high_bet - curr['bet']
        state = {
            "name": curr['name'], "hand": self._get_card_str(curr['hand']), 
            "board": self._get_card_str(self.community_cards),
            "pot": self.pot, "stack": curr['stack'], "to_call": to_call, 
            "history": "\n".join(self.log[-15:]) 
        }
        

        truth = self.get_ground_truth(curr, to_call)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if self.llm:
                    dec = self.llm.get_decision(state, curr['strategy'])
                    act, amt = dec['action'], dec['amount']
                    
                    # === 保存 ML 评估数据 ===
                    curr['ml_data']['y_true_action'].append(truth['action'])
                    curr['ml_data']['y_pred_action'].append(act)
                    
                    true_amt = truth['amount'] if truth['action'] in ['CALL', 'RAISE'] else 0
                    pred_amt = amt if act in ['CALL', 'RAISE'] else 0
                    curr['ml_data']['y_true_amount'].append(true_amt)
                    curr['ml_data']['y_pred_amount'].append(pred_amt)

                else:
                    act = "CHECK" if to_call == 0 else "FOLD"; amt = 0
                    
                if act == "RAISE": 
                    total = curr['bet'] + to_call + max(20, amt)
                    self.execute_move("RAISE", total)
                else: 
                    self.execute_move(act)
                return True
            except Exception as e: 
                logger.warning(f"⚠️ API Retry {attempt+1}/{max_retries} failed: {e}")
                time.sleep(1) 

        self.execute_move("CHECK" if to_call <= 0 else "FOLD")
        return True

    def get_stats(self):
        labels = [h['id'] for h in self.history]
        stats_data = defaultdict(lambda: defaultdict(list))
        for h in self.history:
            for name, stats in h['data'].items():
                for k, v in stats.items():
                    stats_data[k][name].append(v)
        return {"labels": labels, **stats_data}

    def export_data(self, prefix="sim"):
        os.makedirs("exports", exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"exports/{prefix}_{ts}"
        
        stats = self.get_stats()
        try:
            with open(f"{base_name}.json", "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=4, ensure_ascii=False)
        except: pass

        if not stats.get("labels"): return

        try:
            fig, axes = plt.subplots(3, 4, figsize=(26, 18))
            fig.suptitle(f"AI LLM Performance & Poker Metrics ({prefix})", fontsize=24, fontweight='bold')
            ax_flat = axes.flatten()
            
            labels = stats["labels"]
            players = list(stats.get("profit", {}).keys())
            colors = ['#FF9999', '#66B2FF', '#99FF99', '#FFCC99']
            
            def plot_line(ax, metric, title, is_dash=False):
                if metric in stats:
                    for i, player in enumerate(players):
                        if player in stats[metric]:
                            ax.plot(labels, stats[metric][player], label=player, 
                                    color=colors[i % len(colors)], 
                                    linestyle='--' if is_dash else '-', linewidth=2)
                ax.set_title(title, fontweight='bold')
                ax.legend(fontsize=9)
                ax.grid(True, linestyle=':', alpha=0.6)
                
                if metric == "ml_r2":
                    ax.set_ylim(bottom=0, top=1.05)

  
            plot_line(ax_flat[0], "profit", "Net Profit")
            plot_line(ax_flat[1], "win_rate", "Win Rate (%)", True)
            
            ax_style = ax_flat[2]
            if "vpip" in stats and "pfr" in stats:
                for i, player in enumerate(players):
                    if player in stats["vpip"] and player in stats["pfr"]:
                        v = stats["vpip"][player][-1] if stats["vpip"][player] else 0
                        p = stats["pfr"][player][-1] if stats["pfr"][player] else 0
                        ax_style.scatter(v, p, label=player, color=colors[i % len(colors)], s=150, edgecolors='black', zorder=5)
                ax_style.set_xlim(0, 100); ax_style.set_ylim(0, 100)
                ax_style.set_xlabel("VPIP (Loose)"); ax_style.set_ylabel("PFR (Aggr)")
            ax_style.set_title("VPIP vs PFR (Style - Last 20)", fontweight='bold')
            ax_style.legend(fontsize=9)
            ax_style.grid(True, linestyle=':', alpha=0.6)

            ax_bb = ax_flat[3]
            if "bb_100" in stats:
                final_bb = [stats["bb_100"][p][-1] if stats["bb_100"][p] else 0 for p in players]
                ax_bb.bar(players, final_bb, color=[colors[i % len(colors)] for i in range(len(players))], edgecolor='black', zorder=3)
            ax_bb.set_title("BB/100 (Efficiency)", fontweight='bold')
            ax_bb.grid(True, linestyle=':', alpha=0.6, axis='y')

            plot_line(ax_flat[4], "preflop_acc", "Pre-Flop Accuracy (%) - Last 20")
            plot_line(ax_flat[5], "w_sd", "W$SD (Showdown Win %) - Last 20")
            plot_line(ax_flat[6], "wwsf", "WWSF (Pressure Win %) - Last 20", True)
            fig.delaxes(ax_flat[7]) 


            plot_line(ax_flat[8], "ml_accuracy", "Action Accuracy (%) - GTO/Math Basis")
            plot_line(ax_flat[9], "ml_r2", "Bet Size R² (Higher is better)")
            plot_line(ax_flat[10], "ml_mse", "Bet Size MSE (Mean Squared Error)")
            plot_line(ax_flat[11], "ml_mae", "Bet Size MAE (Mean Absolute Error)")

            plt.tight_layout(rect=[0, 0.03, 1, 0.95])
            plt.savefig(f"{base_name}.png", dpi=150, bbox_inches='tight')
            plt.close(fig)

        except Exception as e:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    async def game_loop():
        while True:
            await asyncio.sleep(1)
            if game.hand_active: 
                await run_in_threadpool(game.bot_step)
                
    loop_task = asyncio.create_task(game_loop())
    yield
    loop_task.cancel() 

app = FastAPI(lifespan=lifespan)
game = PokerGame()

os.makedirs("exports", exist_ok=True)
app.mount("/exports", StaticFiles(directory="exports"), name="exports")

@app.get("/")
async def read_root(): return FileResponse("index.html")
    
class ActionReq(BaseModel): action: str; amount: int = 0
class BetReq(BaseModel): target_name: str; amount: int
class ChatReq(BaseModel): target_bot: str; message: str

@app.post("/place_bet")
async def place_bet(req: BetReq):
    if game.user_wallet >= req.amount:
        game.user_wallet -= req.amount
        game.current_bets[req.target_name] = req.amount
        game.log.append(f"YOU BET ${req.amount} ON {req.target_name.upper()}")
    return game.get_state()

@app.post("/chat")
async def chat(req: ChatReq):
    if not game.llm: return {"reply": "System: AI Offline"}
    p = next((x for x in game.players if x['name'] == req.target_bot), None)
    if not p: return {"reply": "System: Bot not found"}
    game.log.append(f"YOU to {req.target_bot}: {req.message}")
    try:
        reply = await run_in_threadpool(game.llm.chat, p['name'], p['strategy'], req.message, game.log[-10:])
        game.log.append(f"{p['name']}: {reply}")
        return {"reply": reply}
    except Exception as e: return {"reply": "..."}
    
@app.get("/state")
async def get_state(): return game.get_state()

@app.get("/stats")
async def get_stats_api(): return game.get_stats()

@app.post("/action")
async def do_action(req: ActionReq):
    game.execute_move(req.action, req.amount)
    return game.get_state()

@app.post("/next")
async def next_hand(): game.start_new_hand(); return game.get_state()

def run_simulation_task(num_hands: int):
    initial_hand = game.hand_count
    target_hand = initial_hand + num_hands
    
    while game.hand_count < target_hand:
        if not game.hand_active: game.start_new_hand()
        curr = game.players[game.current_idx]
        if not curr['is_bot']: game.execute_move("FOLD")
        else: game.bot_step()

    game.export_data(prefix=f"sim_{num_hands}hands")


@app.post("/simulate/{num_hands}")
async def simulate_games(num_hands: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_simulation_task, num_hands)
    return {
        "message": f"Successfully queued simulation of {num_hands} hands", 
        "current_hand": game.hand_count,
        "status": "running in background"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)