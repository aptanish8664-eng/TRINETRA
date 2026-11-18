"""The test-suite safety guard (tests/conftest.py `block_real_wsl`): no test can send a real command to the WSL executor or fleet manager."""
from __future__ import annotations

from app import execution_routes, fleet_routes


def test_the_adapters_cannot_start_wsl_in_tests_a_real_cleanup_is_blocked_and_recorded(blocked_wsl_calls):
    answer = blocked_wsl_calls.real_invoke_mission_executor("cleanup", instance=0)
    assert answer["failure_code"] == "executor_unreachable"                       # answers like an unreachable WSL
    assert blocked_wsl_calls and blocked_wsl_calls[-1][0] == "wsl.exe" and "cleanup" in blocked_wsl_calls[-1]
    land = blocked_wsl_calls.real_invoke_mission_executor("land", instance=0)
    relocate = blocked_wsl_calls.real_invoke_fleet_manager("relocate", count=1, home={"latitude": 47.0, "longitude": 8.0, "altitude_m": 400.0})
    assert land["failure_code"] == "executor_unreachable" and relocate.get("failure")
    assert [call for call in blocked_wsl_calls if "land" in call] and [call for call in blocked_wsl_calls if "relocate" in call]


def test_the_route_level_functions_have_harmless_defaults_in_every_test():
    assert execution_routes.invoke_mission_executor("cleanup", instance=0) == {"cleanup": True, "killed": []}
    assert execution_routes.invoke_mission_executor("land", instance=0)["failure_code"] == "executor_unreachable"
    assert fleet_routes.invoke_fleet_manager("relocate", count=1)["failure_code"] == "manager_unavailable"
