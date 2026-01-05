import json
import re
from openai import OpenAI

class LLMPokerAgent:
    def __init__(self, api_key, base_url, model):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model


    def get_decision(self, game_state, user_instruction, memory=""):
        to_call = game_state['to_call']
        pot = game_state['pot']
        stack = game_state['stack']
        my_name = game_state['name']
        spr = stack / pot if pot > 0 else 100
        

        memory_section = ""
        if memory:
            memory_section = f"""
            ### OPPONENT PROFILING (IMPORTANT):
            Current observations about the Human player:
            {memory}
            
            *ADAPT STRATEGY:* Exploiting these patterns is your WIN CONDITION.
            """
        else:
            memory_section = "### OPPONENT PROFILING:\nNo data yet. Play standard GTO."

        system_prompt = f"""
        You are a World-Class Poker Crusher named "{my_name}". 
        
        ### STRATEGY:
        {user_instruction}
        {memory_section}
    
        ### IDENTITY:
        - "Human" is your opponent. "{my_name}" is YOU.
        - YOUR NAME in the log is "{my_name}". Actions labeled "{my_name}" are YOUR OWN moves.

        ### FORMAT:
        JSON with keys: "action", "amount", "reasoning".

        ### RULES:
        - If to_call > 0, you CANNOT "CHECK".
        """

        user_prompt = f"""
        ### STATE for {my_name}:
        - Stage: {game_state['stage']} | Role: {game_state.get('role', 'Normal')}
        - Hand: {game_state['hand']} | Board: {game_state['board']}
        - Pot: ${pot} | Stack: ${stack} | To Call: ${to_call} | SPR: {spr:.1f}
        
        ### CURRENT HAND HISTORY:
        {game_state['history']}

        What is your move? Return JSON.
        """

        # ==========================================
        #  [DEBUG:  Prompt]
        # ==========================================
        print("\n" + "="*80)
        print(f"🚀 [SENDING TO PLAYING AGENT] ({my_name})")
        print("="*80)
        print(f"--- 🧠 SYSTEM PROMPT ---")
        print(system_prompt.strip())
        print(f"\n--- 🗣️ USER PROMPT ---")
        print(user_prompt.strip())
        print("-" * 80)
        # ==========================================

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            raw_content = response.choices[0].message.content
            print(f"📥 [AI RESPONSE]: {raw_content}")
            
            res_json = json.loads(raw_content)
            
            final_action = str(res_json.get('action', 'FOLD')).upper()
            final_reasoning = res_json.get('reasoning', 'No logic.')
            final_amount = int(res_json.get('amount', 0))


            if to_call == 0 and final_action == "CALL": final_action = "CHECK"
            if to_call > 0 and final_action == "CHECK": final_action = "FOLD"
            if stack == 0: final_action = "CHECK"
            
            return {"action": final_action, "amount": final_amount, "reasoning": final_reasoning}

        except Exception as e:
            print(f"❌ [DECISION ERROR]: {e}")
            return {"action": "CHECK" if to_call == 0 else "FOLD", "amount": 0, "reasoning": f"Error: {e}"}


    def analyze_hand(self, hand_log, winner_info, human_cards="Unknown"):

        system_prompt = """
        You are a Poker Strategy Analyst. 
        Your job is to analyze the "Human" player's behavior in the hand history provided.
        
        ### GOAL:
        Extract **specific, actionable patterns** about the Human's playstyle. 
        Focus on:
        1. Betting patterns (Passive vs Aggressive).
        2. Bluffing frequency (Did they bet with weak hands?).
        3. Showdown value (What hands do they go to showdown with?).
        
        ### OUTPUT FORMAT:
        Return a single, concise sentence (max 20 words) describing the Human's play in this hand.
        Example: "Human played passively pre-flop but over-bet the river with top pair."
        Example: "Human attempted a bluff raise on the turn but folded to a re-raise."
        """

        user_prompt = f"""
        ### HAND LOG:
        {hand_log}
        
        ### RESULT:
        Winner: {winner_info}
        Human's Known Cards: {human_cards}
        
        Summarize the Human's playstyle in this hand:
        """


        print("\n" + "="*80)
        print("🕵️ [SENDING TO ANALYST AGENT]")
        print("="*80)
        print(f"--- 🧠 SYSTEM PROMPT ---")
        print(system_prompt.strip())
        print(f"\n--- 🗣️ USER PROMPT ---")
        print(user_prompt.strip())
        print("-" * 80)

        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5, 
                max_tokens=100   
            )
            
            analysis = response.choices[0].message.content.strip()
            print(f"📝 [ANALYSIS RESULT]: {analysis}")
            print("="*80)
            return analysis

        except Exception as e:
            print(f"❌ [ANALYSIS ERROR]: {e}")
            return "Human played a standard hand."