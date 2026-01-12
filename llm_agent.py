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
        
        # Memory 
        memory_section = ""
        if memory:
            memory_section = f"""
            ### OPPONENT MEMORY:
            Observations about Human:
            {memory}
            """

        system_prompt = f"""
        You are a Texas Hold'em Poker Player named "{my_name}".
        
        ### YOUR PERSONALITY & STRATEGY:
        {user_instruction}
        
        {memory_section}
    
        ### IDENTITY:
        - "Human" is your opponent.
        - Act according to your PERSONALITY. If you are aggressive, bet huge. If passive, check/call.

        ### FORMAT:
        JSON: "action", "amount", "reasoning".

        ### RULES:
        - If to_call > 0, CANNOT "CHECK".
        """

        user_prompt = f"""
        ### STATE for {my_name}:
        - Stage: {game_state['stage']}
        - Hand: {game_state['hand']} | Board: {game_state['board']}
        - Pot: {pot} | Stack: {stack} | To Call: {to_call}
        
        ### HISTORY:
        {game_state['history']}

        Decision (JSON):
        """

        # Debug Print
        print(f"\n [{my_name}] Thinking... Strategy: {user_instruction[:50]}...")

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
            
            res_json = json.loads(response.choices[0].message.content)
            
            final_action = str(res_json.get('action', 'FOLD')).upper()
            final_reasoning = res_json.get('reasoning', 'No logic.')
            final_amount = int(res_json.get('amount', 0))


            if to_call == 0 and final_action == "CALL": final_action = "CHECK"
            if to_call > 0 and final_action == "CHECK": final_action = "FOLD"
            if stack == 0: final_action = "CHECK"
            
            return {"action": final_action, "amount": final_amount, "reasoning": final_reasoning}

        except Exception as e:
            print(f"Error: {e}")
            return {"action": "CHECK" if to_call == 0 else "FOLD", "amount": 0, "reasoning": "Error"}


    def analyze_hand(self, hand_log, winner_info, human_cards="Unknown"):
        system_prompt = """
        You are a Poker Analyst. Summarize the Human's playstyle in 1 sentence based on the log.
        Focus on: Bluffing, Aggression, or Passiveness.
        """
        user_prompt = f"Log: {hand_log}\nWinner: {winner_info}\nHuman Cards: {human_cards}"
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.5, max_tokens=60
            )
            return response.choices[0].message.content.strip()
        except:
            return "Human played normally."