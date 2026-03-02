import json
from openai import OpenAI

class LLMPokerAgent:
    def __init__(self, api_key, base_url, model):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
    def get_decision(self, game_state, user_instruction):
            to_call = game_state.get('to_call', 0)
            my_name = game_state.get('name', 'Bot')
            pot = game_state.get('pot', 0)
            stack = game_state.get('stack', 0)

            system_prompt = f"""
            You are playing Texas Hold'em. Your name is "{my_name}".
            
            ### YOUR PERSONALITY:
            {user_instruction}
            
            ### GAME STATE ANCHORS (PAY ATTENTION):
            - Your current Stack: {stack}
            - Current Pot Size: {pot}
            - Amount to Call: {to_call}
            
            ### CRITICAL BETTING RULES (MUST FOLLOW):
            1. **NEVER bet more than your Stack.** If you want to go All-in, your amount MUST be exactly {stack}.
            2. **Sizing Reference:** If you choose to RAISE, a normal size is between Half-Pot ({max(20, pot//2)}) and Full-Pot ({max(20, pot)}). 
            3. Only go All-in if your personality dictates it or you have an unbeatable hand. Do not invent astronomical numbers.
            
            ### BEHAVIOR RULES:
            1. READ THE LOG HISTORY CAREFULLY.
            2. IF INSULTED: Play AGGRESSIVELY (Raise).
            3. IF BET ON: React based on your personality.
            4. TRASH TALK: Include a short reasoning/trash talk in your output.

            Respond ONLY in valid JSON format: 
            {{"action": "FOLD/CHECK/CALL/RAISE", "amount": <integer>, "reasoning": "<short explanation>"}}
            """
        
            
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": str(game_state)}
                    ],
                    temperature=0.8, 
                    response_format={"type": "json_object"}
                )
                res = json.loads(response.choices[0].message.content)
                
                action = str(res.get('action', 'FOLD')).upper()

                if action not in ["FOLD", "CHECK", "CALL", "RAISE"]:
                    action = "CHECK" if to_call == 0 else "FOLD"
                    
                return {"action": action, "amount": int(res.get('amount', 0))}
                
            except Exception as e:
                print(f"[{my_name}] Decide Error: {e}")
                return {"action": "CHECK" if to_call == 0 else "FOLD", "amount": 0}

    def chat(self, bot_name, persona, user_msg, logs):
        print(f"--- [Chat] {bot_name} receiving: {user_msg} ---")
        prompt = f"""
        You are {bot_name} at a poker table. 
        Persona: {persona}
        Context: {logs}
        
        User said: "{user_msg}"
        
        Reply in character (Max 15 words). 
        If they insulted you, insult back harder. 
        If they bet on you, thank them or tell them to double it.
        """
        try:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=40
            )
            return res.choices[0].message.content.strip()
        except:
            return "..."