"""
Unit tests for Turn-Lock State Machine and Anti-Looping limits.
Verifies topic lock isolation, peer hand-offs, cumulative facts, and the 4-turn hard limit.
"""
from shared.models import AgentTurnMessage, AgentStatus
from orchestrator.turn_lock import TurnLockManager


def test_turn_lock_acquisition_and_isolation():
    manager = TurnLockManager(max_turns=4)
    project_id = "project_test_1"

    # Actor 1 acquires lock
    assert manager.try_acquire_turn(project_id, "pc1") is True

    # Actor 2 cannot acquire while locked by Actor 1
    assert manager.try_acquire_turn(project_id, "vps") is False

    # Same actor can re-check
    assert manager.try_acquire_turn(project_id, "pc1") is True


def test_turn_transition_and_cumulative_facts():
    manager = TurnLockManager(max_turns=4)
    project_id = "project_facts"

    # Turn 1: pc1 gathers disk facts and tags vps
    turn1_msg = AgentTurnMessage(
        project_id=project_id,
        turn_number=1,
        sender_node="pc1",
        status=AgentStatus.IN_PROGRESS,
        next_actor="vps",
        facts={"pc1_disk_free": "120GB"},
        message_for_peer="vps: tolong cek port 8080 di mesin kamu",
        summary="pc1 selesai cek disk."
    )

    result1 = manager.process_turn_result(project_id, turn1_msg)
    assert result1["action"] == "NEXT_TURN"
    assert result1["next_actor"] == "vps"
    assert result1["facts"] == {"pc1_disk_free": "120GB"}

    # Turn 2: vps gathers port facts and tags pc2
    turn2_msg = AgentTurnMessage(
        project_id=project_id,
        turn_number=2,
        sender_node="vps",
        status=AgentStatus.IN_PROGRESS,
        next_actor="pc2",
        facts={"vps_port_8080": "OPEN"},
        message_for_peer="pc2: tolong verifikasi koneksi ke vps:8080",
        summary="vps selesai cek port."
    )

    result2 = manager.process_turn_result(project_id, turn2_msg)
    assert result2["action"] == "NEXT_TURN"
    assert result2["next_actor"] == "pc2"
    assert result2["facts"] == {
        "pc1_disk_free": "120GB",
        "vps_port_8080": "OPEN"
    }


def test_anti_looping_hard_limit():
    manager = TurnLockManager(max_turns=4)
    project_id = "project_loop"

    # Simulate 4 ping-pong turns
    for turn in range(1, 4):
        msg = AgentTurnMessage(
            project_id=project_id,
            turn_number=turn,
            sender_node=f"node_{turn}",
            status=AgentStatus.IN_PROGRESS,
            next_actor=f"node_{turn+1}",
            facts={f"fact_{turn}": f"val_{turn}"},
            summary=f"Turn {turn} complete"
        )
        res = manager.process_turn_result(project_id, msg)
        assert res["action"] == "NEXT_TURN"

    # Turn 4 reaches maximum turns (default = 4)
    turn4_msg = AgentTurnMessage(
        project_id=project_id,
        turn_number=4,
        sender_node="node_4",
        status=AgentStatus.IN_PROGRESS,
        next_actor="node_1",  # attempts to loop back
        facts={"fact_4": "val_4"},
        summary="Turn 4 complete"
    )

    res4 = manager.process_turn_result(project_id, turn4_msg)
    assert res4["action"] == "FORCE_STOP"
    assert "Mencapai batas maksimal" in res4["reason"]
    assert res4["turn_count"] == 4
    assert len(res4["facts"]) == 4


def test_early_exit_on_done_or_approval():
    manager = TurnLockManager(max_turns=4)
    project_id = "project_done"

    done_msg = AgentTurnMessage(
        project_id=project_id,
        turn_number=1,
        sender_node="vps",
        status=AgentStatus.DONE,
        next_actor=None,
        facts={"service_status": "active"},
        summary="Semua pengecekan selesai."
    )

    res = manager.process_turn_result(project_id, done_msg)
    assert res["action"] == "HALT"
    assert res["reason"] == "DONE"
    assert res["summary"] == "Semua pengecekan selesai."
