"""
Pydantic schemas and protocol models for agy-fleet.
"""
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY = "BUSY"
    ERROR = "ERROR"


class WorkMode(str, Enum):
    CONSULT = "CONSULT"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"


class AgentStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    BLOCKED = "BLOCKED"


class NodeHeartbeat(BaseModel):
    node_id: str
    name: str
    device_type: str  # vps, pc, laptop
    os_name: str      # linux, windows, darwin
    ip: str
    status: NodeStatus = NodeStatus.ONLINE
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    agy_version: str = "unknown"
    active_task_id: Optional[str] = None
    timestamp: float = 0.0


class AgentTurnMessage(BaseModel):
    project_id: str
    turn_number: int = 1
    sender_node: str
    status: AgentStatus = AgentStatus.IN_PROGRESS
    next_actor: Optional[str] = None  # e.g. "pc1", "vps", "@online", "@all", or None if DONE
    facts: Dict[str, Any] = Field(default_factory=dict)
    message_for_peer: str = ""
    proposed_plan: Optional[Dict[str, Any]] = None
    summary: str = ""


class CommandStep(BaseModel):
    command: str
    timeout_sec: int = 60
    env_secrets: Dict[str, str] = Field(default_factory=dict)  # ENV_VAR -> VAULT_KEY
    ignore_failure: bool = False
    description: str = ""


class NodeManifest(BaseModel):
    node_id: str
    commands: List[CommandStep] = Field(default_factory=list)
    rollback_commands: List[CommandStep] = Field(default_factory=list)
    verification_commands: List[CommandStep] = Field(default_factory=list)


class ExecutionManifest(BaseModel):
    manifest_id: str
    project_id: str
    created_at: str
    title: str
    description: str = ""
    nodes: Dict[str, NodeManifest] = Field(default_factory=dict)
    hash: str = ""
    approved_by: Optional[int] = None
    approved_at: Optional[str] = None


class StepExecutionResult(BaseModel):
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0
    success: bool = True
    error_message: Optional[str] = None


class ExecutionResult(BaseModel):
    manifest_id: str
    node_id: str
    success: bool
    step_results: List[StepExecutionResult] = Field(default_factory=list)
    verification_results: List[StepExecutionResult] = Field(default_factory=list)
    rollback_performed: bool = False
    rollback_results: List[StepExecutionResult] = Field(default_factory=list)
    error_message: Optional[str] = None


class TurnLockState(BaseModel):
    project_id: str
    locked_by: Optional[str] = None
    turn_count: int = 0
    max_turns: int = 4
    active_actor: Optional[str] = None
    status: AgentStatus = AgentStatus.IN_PROGRESS
    is_active: bool = False
