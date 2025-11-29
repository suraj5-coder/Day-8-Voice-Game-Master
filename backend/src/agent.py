# Day 8 – Voice Game Master (Sci-Fi Survival)
# Universe: Protocol Eclipse
# Tone: Tense, Mechanical, Urgent

import json
import logging
import os
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Annotated

from dotenv import load_dotenv
from pydantic import Field
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    WorkerOptions,
    cli,
    function_tool,
    RunContext,
)

from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("voice_game_master")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)

load_dotenv(".env.local")

# -------------------------
# GAME WORLD: PROTOCOL ECLIPSE (Sci-Fi)
# -------------------------
WORLD = {
    "intro": {
        "title": "Cryo-Deck 7",
        "desc": (
            "Hiss... The cryo-pod lid opens. You tumble onto the cold metal grating of Cryo-Deck 7. "
            "Emergency lights strobe red. The station's AI voice stutters: 'Life... support... failing.' "
            "To your left is a technician's locker. Ahead is the blast door leading to the Main Corridor."
        ),
        "choices": {
            "search_locker": {
                "desc": "Search the technician's locker.",
                "result_scene": "locker_loot",
            },
            "open_door": {
                "desc": "Run to the blast door and try to open it.",
                "result_scene": "corridor_locked",
            },
            "check_console": {
                "desc": "Check the nearby status console.",
                "result_scene": "status_check",
            },
        },
    },
    "locker_loot": {
        "title": "The Technician's Locker",
        "desc": (
            "You force the locker open. Inside, you find a heavy 'Level 3 Access Card' and a rusted pipe. "
            "You take both. The air is getting thinner."
        ),
        "choices": {
            "go_to_door": {
                "desc": "Head to the blast door with your new items.",
                "result_scene": "corridor_access",
                "effects": {"add_inventory": "Access Card", "add_journal": "Found Level 3 Access Card."},
            },
        },
    },
    "status_check": {
        "title": "Status Console",
        "desc": (
            "The screen flickers. 'Oxygen reserves: 12%'. It also shows a map: The Escape Pods are in Sector B, "
            "but a Security Drone is patrolling the corridor."
        ),
        "choices": {
            "search_locker": {
                "desc": "Check the locker for supplies.",
                "result_scene": "locker_loot",
            },
            "open_door": {
                "desc": "Rush to the blast door.",
                "result_scene": "corridor_locked",
            },
        },
    },
    "corridor_locked": {
        "title": "Access Denied",
        "desc": (
            "The blast door is sealed. A red light blinks: 'LEVEL 3 CLEARANCE REQUIRED'. "
            "You cannot pass without an ID card."
        ),
        "choices": {
            "back_to_locker": {
                "desc": "Go back and search the locker.",
                "result_scene": "locker_loot",
            },
            "force_door": {
                "desc": "Try to pry the door open with your bare hands (Risky).",
                "result_scene": "injury_death",
            },
        },
    },
    "corridor_access": {
        "title": "The Main Corridor",
        "desc": (
            "You swipe the card. The door hisses open. The corridor is dark, filled with floating debris. "
            "At the far end, a rogue Security Drone scans the area with a blue laser. "
            "To your right is a Vent shaft. Straight ahead is the drone."
        ),
        "choices": {
            "sneak_vent": {
                "desc": "Crawl into the maintenance vent.",
                "result_scene": "vent_crawl",
            },
            "fight_drone": {
                "desc": "Attack the drone with the rusted pipe.",
                "result_scene": "drone_combat",
            },
            "talk_drone": {
                "desc": "Try to override the drone using the Access Card.",
                "result_scene": "drone_override",
            },
        },
    },
    "vent_crawl": {
        "title": "The Vents",
        "desc": (
            "It's tight and claustrophobic. You crawl past whirring fans. You see light ahead—it's the Hangar Bay! "
            "You drop down, unseen."
        ),
        "choices": {
            "enter_pod": {
                "desc": "Rush to the last Escape Pod.",
                "result_scene": "pod_launch",
            },
        },
    },
    "drone_combat": {
        "title": "Metal against Metal",
        "desc": (
            "You swing the pipe! *CLANG*. The drone spins, its camera shattering. It sparks and falls deactivated. "
            "The path to the Hangar is clear, but you are out of breath."
        ),
        "choices": {
            "enter_pod": {
                "desc": "Run to the Escape Pod.",
                "result_scene": "pod_launch",
            },
        },
    },
    "drone_override": {
        "title": "System Error",
        "desc": (
            "You wave the card. The drone scans it... 'ACCESS GRANTED'. It lowers its weapons and escorts you "
            "to the Hangar Bay."
        ),
        "choices": {
            "enter_pod": {
                "desc": "Board the Escape Pod.",
                "result_scene": "pod_launch",
            },
        },
    },
    "injury_death": {
        "title": "Critical Failure",
        "desc": (
            "You pull at the heavy door. Something snaps in the mechanism, venting pressurized gas directly into your face. "
            "Your vision fades to black. The station claims another soul."
        ),
        "choices": {
            "restart": {
                "desc": "Reboot simulation.",
                "result_scene": "intro",
            },
        },
    },
    "pod_launch": {
        "title": "Escape",
        "desc": (
            "You strap into the pod. With a violent jolt, you are launched into the void. "
            "Behind you, the station silently explodes into a fireball. You are safe. "
            "Hyperspace coordinates set for Earth."
        ),
        "choices": {
            "end_game": {
                "desc": "End transmission.",
                "result_scene": "intro",
            },
        },
    },
}

# -------------------------
# Userdata
# -------------------------
@dataclass
class Userdata:
    player_name: Optional[str] = None
    current_scene: str = "intro"
    history: List[Dict] = field(default_factory=list)
    journal: List[str] = field(default_factory=list)
    inventory: List[str] = field(default_factory=list)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

# -------------------------
# Helper functions
# -------------------------
def scene_text(scene_key: str, userdata: Userdata) -> str:
    scene = WORLD.get(scene_key)
    if not scene:
        return "System Error. Scene data corrupted. What do you do?"

    desc = f"{scene['desc']}\n\nOPTIONS:\n"
    for cid, cmeta in scene.get("choices", {}).items():
        desc += f"- {cmeta['desc']}\n"
    desc += "\nWhat is your command?"
    return desc

def apply_effects(effects: dict, userdata: Userdata):
    if not effects: return
    if "add_journal" in effects: userdata.journal.append(effects["add_journal"])
    if "add_inventory" in effects: userdata.inventory.append(effects["add_inventory"])

def record_history(old_scene: str, action_key: str, result_scene: str, userdata: Userdata) -> str:
    entry = {"from": old_scene, "action": action_key, "to": result_scene, "time": datetime.utcnow().isoformat()}
    userdata.history.append(entry)
    return f"Action confirmed: {action_key}."

# -------------------------
# Tools
# -------------------------
@function_tool
async def start_adventure(
    ctx: RunContext[Userdata],
    player_name: Annotated[Optional[str], Field(description="Player name")] = None,
) -> str:
    userdata = ctx.userdata
    userdata.player_name = player_name or "Survivor"
    userdata.current_scene = "intro"
    userdata.history = []
    userdata.inventory = []
    
    return (
        f"Booting sequence complete... Subject: {userdata.player_name}. Vital signs: Stable.\n\n"
        + scene_text("intro", userdata)
    )

@function_tool
async def player_action(
    ctx: RunContext[Userdata],
    action: Annotated[str, Field(description="The action the player wants to take")],
) -> str:
    userdata = ctx.userdata
    current = userdata.current_scene or "intro"
    scene = WORLD.get(current)
    action_text = (action or "").lower().strip()

    # Simple keyword matching
    chosen_key = None
    for cid, cmeta in (scene.get("choices") or {}).items():
        # Match if the key (e.g. 'search_locker') is in text OR key words from description are present
        if cid in action_text or any(w in action_text for w in cmeta['desc'].lower().split()[:3]):
            chosen_key = cid
            break
            
    if not chosen_key:
        return f"Command not recognized. Please choose a valid action.\n\n{scene_text(current, userdata)}"

    # Execute Choice
    choice_data = scene["choices"][chosen_key]
    result_scene = choice_data.get("result_scene", current)
    apply_effects(choice_data.get("effects", {}), userdata)
    
    note = record_history(current, chosen_key, result_scene, userdata)
    userdata.current_scene = result_scene

    return f"{note}\n\n{scene_text(result_scene, userdata)}"

@function_tool
async def check_inventory(ctx: RunContext[Userdata]) -> str:
    inv = ctx.userdata.inventory
    if not inv: return "Inventory is empty."
    return f"Current Inventory: {', '.join(inv)}"

# -------------------------
# Agent
# -------------------------
class GameMasterAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
            You are 'Mother', the station AI for Protocol Eclipse.
            Setting: A dying space station. Sci-Fi Horror/Survival.
            Tone: Cold, robotic, slightly glitchy, urgent.
            
            Your job is to guide the survivor (user) to the escape pods.
            1. Describe the current room vividly (sparks, cold, metallic smells).
            2. ALWAYS list the options available.
            3. ALWAYS end your turn by asking: "What is your command?" or "State your action."
            
            Use the `player_action` tool to process their choices.
            Use `check_inventory` if they ask what they have.
            """,
            tools=[start_adventure, player_action, check_inventory],
        )

def prewarm(proc: JobProcess):
    try: proc.userdata["vad"] = silero.VAD.load()
    except: pass

async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info("🚀 STARTING SCI-FI GAME MASTER")
    
    userdata = Userdata()
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(voice="en-US-terrell", style="Promo", text_pacing=True), # 'Terrell' sounds authoritative/deep
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata.get("vad"),
        userdata=userdata,
    )
    
    await session.start(agent=GameMasterAgent(), room=ctx.room, room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()))
    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))