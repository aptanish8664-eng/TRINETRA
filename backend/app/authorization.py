from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    MISSION_READ_OWN = "mission.read_own"
    MISSION_READ_ALL = "mission.read_all"
    MISSION_CREATE = "mission.create"
    SIMULATION_CONTROL = "simulation.control"
    EXPERIMENT_CREATE = "experiment.create"
    ANALYTICS_EXPORT = "analytics.export"
    AI_PROPOSE = "ai.propose"
    AI_REVIEW = "ai.review"
    THREAT_ACKNOWLEDGE = "threat.acknowledge"
    THREAT_ISOLATE = "threat.isolate"
    TASK_REASSIGN = "task.reassign"
    COMMAND_REQUEST = "command.request"
    COMMAND_APPROVE = "command.approve"
    USER_MANAGE = "user.manage"
    REGISTRATION_REVIEW = "registration.review"
    SCENARIO_CREATE = "scenario.create"
    SCENARIO_REVIEW = "scenario.review"
    SCENARIO_EXECUTE = "scenario.execute"
    AUDIT_READ = "audit.read"


_OPERATOR_PERMISSIONS = frozenset({
    Permission.MISSION_READ_OWN,
    Permission.MISSION_CREATE,
    Permission.SIMULATION_CONTROL,
    Permission.EXPERIMENT_CREATE,
    Permission.ANALYTICS_EXPORT,
    Permission.AI_PROPOSE,
    Permission.COMMAND_REQUEST,
    Permission.SCENARIO_CREATE,
    Permission.SCENARIO_EXECUTE,
})

_GENERAL_PERMISSIONS = frozenset(Permission) - {Permission.COMMAND_REQUEST}


def permissions_for(role: str) -> frozenset[Permission]:
    if role == "GENERAL":
        return _GENERAL_PERMISSIONS
    if role == "OPERATOR":
        return _OPERATOR_PERMISSIONS
    return frozenset()


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for(role)
