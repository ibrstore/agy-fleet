"""
Turn-Lock State Machine for Peer-to-Peer Agent Discussions.
Prevents infinite loops, race conditions, and uncontrolled token consumption.
Enforces:
1. Exactly 1 active turn per project/topic thread.
2. Hard Limit of 4 turns max (configurable).
3. Cumulative fact ledger across turns.
"""
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from shared.config import DEFAULT_MAX_TURNS
from shared.models import AgentTurnMessage, AgentStatus, TurnLockState


@dataclass
class TurnSession:
    project_id: str
    max_turns: int = DEFAULT_MAX_TURNS
    turn_count: int = 0
    active_actor: Optional[str] = None
    status: AgentStatus = AgentStatus.IN_PROGRESS
    cumulative_facts: Dict[str, Any] = field(default_factory=dict)
    history: list = field(default_factory=list)
    is_locked: bool = False


class TurnLockManager:
    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS):
        self.default_max_turns = max_turns
        self.sessions: Dict[str, TurnSession] = {}

    def get_or_create_session(self, project_id: str, max_turns: Optional[int] = None) -> TurnSession:
        if project_id not in self.sessions:
            self.sessions[project_id] = TurnSession(
                project_id=project_id,
                max_turns=max_turns or self.default_max_turns
            )
        return self.sessions[project_id]

    def try_acquire_turn(self, project_id: str, actor: str) -> bool:
        """Attempt to lock the active turn for a specific actor."""
        session = self.get_or_create_session(project_id)
        if session.is_locked and session.active_actor != actor:
            return False  # Already locked by another actor
        session.is_locked = True
        session.active_actor = actor
        return True

    def process_turn_result(self, project_id: str, turn_msg: AgentTurnMessage) -> Dict[str, Any]:
        """
        Process incoming agent turn message, update facts, check loop limits,
        and determine next step.
        """
        session = self.get_or_create_session(project_id)
        session.turn_count += 1
        session.history.append(turn_msg.model_dump())

        # Merge newly gathered facts into cumulative facts ledger
        if turn_msg.facts:
            session.cumulative_facts.update(turn_msg.facts)

        session.status = turn_msg.status

        # 1. Check if agent finished or requested approval
        if turn_msg.status in [AgentStatus.DONE, AgentStatus.NEEDS_APPROVAL, AgentStatus.BLOCKED]:
            session.is_locked = False
            session.active_actor = None
            return {
                "action": "HALT",
                "reason": turn_msg.status.value,
                "turn_count": session.turn_count,
                "facts": session.cumulative_facts,
                "summary": turn_msg.summary,
                "proposed_plan": turn_msg.proposed_plan
            }

        # 2. Check Hard Turn Limit (Anti-Looping)
        if session.turn_count >= session.max_turns:
            session.status = AgentStatus.DONE
            session.is_locked = False
            session.active_actor = None
            return {
                "action": "FORCE_STOP",
                "reason": f"Mencapai batas maksimal {session.max_turns} putaran diskusi.",
                "turn_count": session.turn_count,
                "facts": session.cumulative_facts,
                "summary": turn_msg.summary or "Diskusi dihentikan otomatis pada batas putaran.",
                "proposed_plan": turn_msg.proposed_plan
            }

        # 3. Continue to next actor if specified
        next_actor = turn_msg.next_actor
        if not next_actor or next_actor.lower() in ["none", "null"]:
            session.is_locked = False
            session.active_actor = None
            return {
                "action": "HALT",
                "reason": "Tidak ada next_actor yang ditentukan.",
                "turn_count": session.turn_count,
                "facts": session.cumulative_facts,
                "summary": turn_msg.summary,
                "proposed_plan": turn_msg.proposed_plan
            }

        # Hand over lock to next actor
        session.active_actor = next_actor
        session.is_locked = True
        return {
            "action": "NEXT_TURN",
            "next_actor": next_actor,
            "turn_count": session.turn_count,
            "facts": session.cumulative_facts,
            "message_for_peer": turn_msg.message_for_peer,
            "summary": turn_msg.summary
        }

    def release_lock(self, project_id: str) -> None:
        if project_id in self.sessions:
            self.sessions[project_id].is_locked = False
            self.sessions[project_id].active_actor = None

    def reset_session(self, project_id: str) -> None:
        if project_id in self.sessions:
            del self.sessions[project_id]
