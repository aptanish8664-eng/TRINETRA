from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import platform
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .auth_routes import require_permission, require_csrf, router as auth_router
from .authorization import Permission, has_permission
from .ai.routes import router as ai_router
from .config import (
    ALLOWED_ORIGINS,
    AUTO_START_DEFAULT_SITL,
    GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP,
    GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT,
    GAZEBO_POSE_UDP_BIND_HOST,
    GAZEBO_POSE_UDP_BIND_PORT,
    GAZEBO_POSE_UDP_ENABLED,
    MAX_HTTP_BODY_BYTES,
    OPENAI_API_KEY,
    PROJECT_ROOT,
    PX4_UDP_ALLOWED_SOURCE_IP,
    PX4_UDP_ALLOWED_SOURCE_PORT,
    PX4_UDP_BIND_HOST,
    PX4_UDP_BIND_PORT,
    PX4_UDP_ENABLED,
    TRINETRA_ML_MODEL_PATH,
)
from .persistence.database import (
    add_audit_event,
    add_experiment_owner,
    add_run_owner,
    begin_write_transaction,
    CommandDecisionRecord,
    get_experiment,
    get_replay,
    list_runs,
    run_owner,
    save_events,
    save_experiment,
    save_run,
    save_snapshot,
    engine,
    SessionLocal,
    utcnow_naive,
    UserRecord,
)
from .schemas import CommandDecisionCreateRequest, CommandDecisionReviewRequest, ExperimentRequest, SimulationRequest, TaskReassignmentRequest
from .security import AuthContext, get_auth_context
from .scenario_routes import router as scenario_router
from .fleet_routes import router as fleet_router
from .mission_plan_routes import router as mission_plan_router
from .attack_lab_routes import router as attack_lab_router
from .research_routes import router as research_router
from .simulation.detector import DETECTOR_VERSION
from .simulation.engine import SIMULATION_VERSION, Simulation, SimulationConfig
from .simulation.response import reassignment_block_reason, reassign_tasks
from .research.model import load_model
from .telemetry.px4 import Px4MavlinkProtocol, Px4TelemetryStore
from .telemetry.gazebo import GazeboPoseProtocol, GazeboPoseStore


WEBSOCKET_SEND_QUEUE_SIZE = 4
WEBSOCKET_SEND_TIMEOUT_SECONDS = 5.0
http_request_logger = logging.getLogger("trinetra.http")


def _load_configured_ml_model() -> dict[str, Any] | None:
    if not TRINETRA_ML_MODEL_PATH:
        return None
    configured = (PROJECT_ROOT / TRINETRA_ML_MODEL_PATH).resolve()
    if not configured.is_relative_to(PROJECT_ROOT.resolve()):
        raise RuntimeError("TRINETRA_ML_MODEL_PATH must point inside the project workspace")
    try:
        return load_model(configured)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Configured TRINETRA ML model is unavailable or invalid") from exc


ACTIVE_ML_MODEL = _load_configured_ml_model()


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    await start_px4_telemetry_receiver()
    await start_gazebo_pose_receiver()
    startup_task = asyncio.create_task(_initialize_default_sitl())
    try:
        yield
    finally:
        startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass
        await stop_gazebo_pose_receiver()
        await stop_px4_telemetry_receiver()


app = FastAPI(
    title="TRINETRA Simulation API",
    version="0.1.0",
    description="Software-only, deterministic multi-UAV GPS resilience research demonstrator.",
    lifespan=app_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    allow_credentials=True,
    expose_headers=["X-Request-ID"],
)
app.include_router(auth_router)
app.include_router(ai_router)
app.include_router(scenario_router)
app.include_router(fleet_router)
app.include_router(mission_plan_router)
app.include_router(attack_lab_router)
app.include_router(research_router)


class ManagedRun:
    def __init__(self, simulation: Simulation):
        self.simulation = simulation
        self.lock = asyncio.Lock()
        self.sockets: set[StreamClient] = set()
        self.runner: asyncio.Task | None = None
        self.event_cursor = 0

    async def persist(self, snapshot: bool = True) -> dict:
        view = self.simulation.view()
        save_events(self.simulation_id, self.simulation.events[self.event_cursor:])
        self.event_cursor = len(self.simulation.events)
        if snapshot:
            save_snapshot(self.simulation_id, view)
        return view

    @property
    def simulation_id(self) -> str:
        return self.simulation._run_id


@dataclass(eq=False)
class StreamClient:
    websocket: WebSocket
    outbound: asyncio.Queue[dict] = field(
        default_factory=lambda: asyncio.Queue(maxsize=WEBSOCKET_SEND_QUEUE_SIZE),
    )
    sender: asyncio.Task | None = None
    dropped_states: int = 0


runs: dict[str, ManagedRun] = {}
px4_telemetry_store = Px4TelemetryStore()
px4_udp_transport: asyncio.DatagramTransport | None = None
px4_udp_protocol: Px4MavlinkProtocol | None = None
px4_udp_error_code: str | None = None
gazebo_pose_store = GazeboPoseStore()
gazebo_pose_udp_transport: asyncio.DatagramTransport | None = None
gazebo_pose_udp_protocol: GazeboPoseProtocol | None = None
gazebo_pose_udp_error_code: str | None = None
default_sitl_startup_status: dict[str, Any] = {
    "status": "STARTING", "aircraft_id": None, "detail": "Backend startup has not run yet.",
}


async def _initialize_default_sitl() -> None:
    global default_sitl_startup_status
    if not AUTO_START_DEFAULT_SITL:
        default_sitl_startup_status = {"status": "DEGRADED", "aircraft_id": None, "detail": "Automatic default SITL startup is disabled."}
        return
    if px4_udp_transport is None or gazebo_pose_udp_transport is None:
        default_sitl_startup_status = {
            "status": "DEGRADED", "aircraft_id": None,
            "detail": "PX4 telemetry and Gazebo pose receivers must both be configured before default SITL startup.",
        }
        return
    default_sitl_startup_status = {"status": "STARTING", "aircraft_id": None, "detail": "Requesting one managed disarmed PX4/Gazebo instance."}
    try:
        from .telemetry.fleet_manager import invoke_fleet_manager
        manager = await asyncio.to_thread(invoke_fleet_manager, "activate", count=1, timeout_seconds=35)
        if manager.get("failure"):
            default_sitl_startup_status = {"status": "FAILED", "aircraft_id": None, "detail": str(manager["failure"])[:240]}
            return
        from .fleet_routes import _verified_fleet
        deadline = time.monotonic() + 45
        while True:
            verified, _ = _verified_fleet(1)
            if verified:
                default_sitl_startup_status = {
                    "status": "READY", "aircraft_id": verified[0]["aircraft_id"],
                    "system_id": verified[0]["system_id"], "gazebo_model": verified[0]["gazebo_model"],
                    "detail": "PX4 process, telemetry position, and Gazebo model verified.",
                }
                return
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        default_sitl_startup_status = {"status": "DEGRADED", "aircraft_id": None, "detail": "PX4 process, telemetry, and Gazebo pose did not all verify before timeout."}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        default_sitl_startup_status = {
            "status": "FAILED", "aircraft_id": None,
            "detail": f"Default SITL initialization failed ({type(exc).__name__}).",
        }


async def start_px4_telemetry_receiver() -> None:
    global px4_udp_transport, px4_udp_protocol, px4_udp_error_code
    if not PX4_UDP_ENABLED:
        px4_udp_error_code = None
        return
    if not PX4_UDP_ALLOWED_SOURCE_IP:
        px4_udp_error_code = "allowed_source_not_configured"
        logging.getLogger("trinetra.telemetry.px4").error(
            "PX4 telemetry remains unavailable: allowed source IP is not configured",
        )
        return
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: Px4MavlinkProtocol(
                px4_telemetry_store,
                allowed_source_ip=PX4_UDP_ALLOWED_SOURCE_IP,
                allowed_source_port=PX4_UDP_ALLOWED_SOURCE_PORT,
            ),
            local_addr=(PX4_UDP_BIND_HOST, PX4_UDP_BIND_PORT),
        )
    except OSError as exc:
        px4_udp_error_code = "udp_bind_failed"
        logging.getLogger("trinetra.telemetry.px4").error(
            "PX4 telemetry UDP listener could not bind (%s)", type(exc).__name__,
        )
        return
    px4_udp_transport = transport
    px4_udp_protocol = protocol
    px4_udp_error_code = None
    _LOGGER = logging.getLogger("trinetra.telemetry.px4")
    _LOGGER.info("PX4 MAVLink receiver listening on %s:%s", PX4_UDP_BIND_HOST, PX4_UDP_BIND_PORT)


async def stop_px4_telemetry_receiver() -> None:
    global px4_udp_transport, px4_udp_protocol
    if px4_udp_transport is not None:
        px4_udp_transport.close()
        px4_udp_transport = None
        px4_udp_protocol = None


async def start_gazebo_pose_receiver() -> None:
    global gazebo_pose_udp_transport, gazebo_pose_udp_protocol, gazebo_pose_udp_error_code
    if not GAZEBO_POSE_UDP_ENABLED:
        gazebo_pose_udp_error_code = None
        return
    if not GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP:
        gazebo_pose_udp_error_code = "allowed_source_not_configured"
        logging.getLogger("trinetra.telemetry.gazebo").error(
            "Gazebo pose receiver remains unavailable: allowed source IP is not configured",
        )
        return
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: GazeboPoseProtocol(
                gazebo_pose_store,
                allowed_source_ip=GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP,
                allowed_source_port=GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT,
            ),
            local_addr=(GAZEBO_POSE_UDP_BIND_HOST, GAZEBO_POSE_UDP_BIND_PORT),
        )
    except OSError as exc:
        gazebo_pose_udp_error_code = "udp_bind_failed"
        logging.getLogger("trinetra.telemetry.gazebo").error(
            "Gazebo pose UDP listener could not bind (%s)", type(exc).__name__,
        )
        return
    gazebo_pose_udp_transport = transport
    gazebo_pose_udp_protocol = protocol
    gazebo_pose_udp_error_code = None
    logging.getLogger("trinetra.telemetry.gazebo").info(
        "Gazebo pose receiver listening on %s:%s", GAZEBO_POSE_UDP_BIND_HOST, GAZEBO_POSE_UDP_BIND_PORT,
    )


async def stop_gazebo_pose_receiver() -> None:
    global gazebo_pose_udp_transport, gazebo_pose_udp_protocol
    if gazebo_pose_udp_transport is not None:
        gazebo_pose_udp_transport.close()
        gazebo_pose_udp_transport = None
        gazebo_pose_udp_protocol = None


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    started_at = time.perf_counter()
    status_code = 500
    try:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                parsed_content_length = int(content_length)
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
            else:
                if parsed_content_length > MAX_HTTP_BODY_BYTES:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "Request body exceeds the configured size limit."},
                    )
                else:
                    response = await call_next(request)
        else:
            response = await call_next(request)

        status_code = response.status_code
        response.headers["X-Request-ID"] = request.state.request_id
        return response
    finally:
        http_request_logger.info(json.dumps({
            "event": "http_request",
            "request_id": request.state.request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
        }, separators=(",", ":")))


def _new_run(config: SimulationConfig, owner_id: str) -> ManagedRun:
    run_id = uuid.uuid4().hex[:12]
    sim = Simulation(config, detector_model=ACTIVE_ML_MODEL)
    sim._run_id = run_id
    managed = ManagedRun(sim)
    runs[run_id] = managed
    save_run(run_id, asdict(config))
    add_run_owner(run_id, owner_id)
    save_snapshot(run_id, sim.view())
    save_events(run_id, sim.events)
    managed.event_cursor = len(sim.events)
    return managed


def _get(run_id: str) -> ManagedRun:
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Active simulation not found")
    return runs[run_id]


def _require_run_owner(run_id: str, context: AuthContext, request: Request | WebSocket | None = None) -> ManagedRun:
    managed = _get(run_id)
    owner_id = run_owner(run_id)
    can_access_all = has_permission(
        context.user.role, Permission.MISSION_READ_ALL,
    )
    if (owner_id is None and not can_access_all) or (owner_id is not None and owner_id != context.user.id and not can_access_all):
        if request is not None:
            from .security import audit_denial

            audit_denial(request, "mission_resource_access_denied", context.user)
        raise HTTPException(status_code=404, detail="Simulation not found")
    return managed


def _record_operator_action(request: Request, context: AuthContext, action: str, target_type: str, target_id: str, reason: str) -> None:
    add_audit_event(
        action=action, outcome="success", reason=reason,
        request_id=getattr(request.state, "request_id", "request-unavailable"),
        actor_id=context.user.id, actor_name=context.user.full_name,
        target_type=target_type, target_id=target_id,
    )


def _record_operator_denial(
    request: Request, context: AuthContext, action: str, target_type: str, target_id: str, reason: str,
) -> None:
    add_audit_event(
        action=action, outcome="denied", reason=reason,
        request_id=getattr(request.state, "request_id", "request-unavailable"),
        actor_id=context.user.id, actor_name=context.user.full_name,
        target_type=target_type, target_id=target_id,
    )


async def _broadcast(managed: ManagedRun, state: dict) -> None:
    for client in tuple(managed.sockets):
        if client.sender is not None and client.sender.done():
            managed.sockets.discard(client)
            continue
        try:
            client.outbound.put_nowait(state)
        except asyncio.QueueFull:
            # State is replaceable: retain the newest snapshot instead of letting
            # one slow observer stall the simulation or other WebSocket clients.
            try:
                client.outbound.get_nowait()
            except asyncio.QueueEmpty:
                pass
            else:
                client.dropped_states += 1
            try:
                client.outbound.put_nowait(state)
            except asyncio.QueueFull:
                client.dropped_states += 1


async def _stream_sender(client: StreamClient) -> None:
    close_code = None
    close_reason = None
    try:
        while True:
            state = await client.outbound.get()
            await asyncio.wait_for(
                client.websocket.send_json(state),
                timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
            )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        close_code = 1013
        close_reason = "Telemetry stream client could not keep up."
    except Exception:
        close_code = 1011
        close_reason = "Telemetry stream send failed."
    finally:
        if close_code is not None:
            try:
                await client.websocket.close(code=close_code, reason=close_reason)
            except Exception:
                pass


async def _run_clock(run_id: str, managed: ManagedRun) -> None:
    try:
        while managed.simulation.status == "running":
            await asyncio.sleep(0.25)
            async with managed.lock:
                if managed.simulation.status != "running":
                    break
                managed.simulation.step()
                state = await managed.persist()
            await _broadcast(managed, state)
    finally:
        managed.runner = None


@app.get("/api/health")
def health() -> dict:
    database_status = "healthy"
    try:
        with SessionLocal() as db:
            db.execute(select(1))
    except SQLAlchemyError:
        database_status = "unavailable"

    running_simulations = sum(run.simulation.status == "running" for run in runs.values())
    websocket_clients = sum(len(run.sockets) for run in runs.values())
    detector_mode = "trained_ml" if ACTIVE_ML_MODEL is not None else "heuristic"
    if px4_udp_transport is None:
        receiver_status = "unavailable" if PX4_UDP_ENABLED else "not_integrated"
        px4_component = {"status": receiver_status}
        if px4_udp_error_code is not None:
            px4_component["error_code"] = px4_udp_error_code
        telemetry_component = {"status": receiver_status, "freshness": "not_available"}
    else:
        px4_snapshot = px4_telemetry_store.snapshot()
        connected_count = sum(vehicle["status"] == "connected" for vehicle in px4_snapshot["vehicles"])
        stale_count = sum(vehicle["status"] == "stale" for vehicle in px4_snapshot["vehicles"])
        receiving = px4_snapshot["transport"]["status"] == "receiving"
        px4_component = {
            "status": "receiving" if receiving else px4_snapshot["transport"]["status"],
            "vehicle_count": len(px4_snapshot["vehicles"]),
        }
        telemetry_component = {
            "status": "receiving" if receiving else px4_snapshot["transport"]["status"],
            "freshness": "fresh" if connected_count else "stale" if stale_count else "not_available",
            "connected_vehicles": connected_count,
            "stale_vehicles": stale_count,
        }
    if gazebo_pose_udp_transport is None:
        gazebo_status = "unavailable" if GAZEBO_POSE_UDP_ENABLED else "not_integrated"
        gazebo_component = {"status": gazebo_status}
        if gazebo_pose_udp_error_code is not None:
            gazebo_component["error_code"] = gazebo_pose_udp_error_code
    else:
        gazebo_snapshot = gazebo_pose_store.snapshot()
        gazebo_component = {
            "status": gazebo_snapshot["transport"]["status"],
            "world": gazebo_snapshot["world"],
            "model_count": len(gazebo_snapshot["models"]),
        }
    components = {
        "api": {"status": "available"},
        "database": {"status": database_status, "dialect": engine.dialect.name},
        "simulation": {
            "status": "available",
            "kind": "abstract_software",
            "managed_runs": len(runs),
            "running_runs": running_simulations,
        },
        "websocket": {"status": "available", "active_clients": websocket_clients},
        "detector": {
            "status": "ready",
            "mode": detector_mode,
            "model_id": ACTIVE_ML_MODEL["model_id"] if ACTIVE_ML_MODEL is not None else None,
            "confidence_calibrated": False if ACTIVE_ML_MODEL is not None else None,
        },
        "ai_provider": {"status": "configured" if OPENAI_API_KEY else "not_configured"},
        "px4": px4_component,
        "telemetry": telemetry_component,
        "gazebo": gazebo_component,
        "default_sitl_startup": dict(default_sitl_startup_status),
    }
    payload = {
        "status": "ready" if database_status == "healthy" else "degraded",
        "active_simulations": len(runs),
        "database": engine.dialect.name,
        "database_status": database_status,
        "detector_mode": detector_mode,
        "model_id": ACTIVE_ML_MODEL["model_id"] if ACTIVE_ML_MODEL is not None else None,
        "components": components,
    }
    if database_status != "healthy":
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/api/telemetry/px4")
def get_px4_telemetry(
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    if px4_udp_transport is None:
        raise HTTPException(status_code=503, detail="PX4 telemetry receiver is not enabled")
    return px4_telemetry_store.snapshot()


@app.get("/api/telemetry/gazebo")
def get_gazebo_pose_snapshot(
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    if gazebo_pose_udp_transport is None:
        raise HTTPException(status_code=503, detail="Gazebo pose receiver is not enabled")
    return gazebo_pose_store.snapshot()


@app.post("/api/simulations")
async def create_simulation(
    request: SimulationRequest,
    http_request: Request,
    context: AuthContext = Depends(require_permission(Permission.MISSION_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        config = SimulationConfig(**request.model_dump())
        managed = _new_run(config, context.user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _record_operator_action(http_request, context, "mission_created", "run", managed.simulation_id, "Operator created a ready simulation")
    return {"id": managed.simulation_id, "state": managed.simulation.view()}


@app.get("/api/simulations/{run_id}")
def get_state(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    return {"id": run_id, "state": _require_run_owner(run_id, context).simulation.view()}


async def _control(run_id: str, action: str, request: Request, context: AuthContext) -> dict:
    managed = _get(run_id)
    async with managed.lock:
        sim = managed.simulation
        allowed = {
            "start": sim.status == "ready",
            "pause": sim.status == "running",
            "resume": sim.status == "paused",
            "step": sim.status in {"ready", "running", "paused"},
            "stop": sim.status not in {"completed", "stopped"},
            "reset": True,
        }
        if not allowed[action]:
            _record_operator_denial(
                request, context, f"mission_{action}", "run", run_id,
                "Requested mission lifecycle transition is invalid for the current mission state.",
            )
            raise HTTPException(status_code=409, detail=f"Cannot {action} a mission in {sim.status} state.")
        if action == "start":
            sim.start()
        elif action == "pause":
            sim.pause()
        elif action == "resume":
            sim.start()
        elif action == "step":
            sim.step()
        elif action == "stop":
            sim.stop()
        elif action == "reset":
            sim.reset()
        state = await managed.persist()
    if action in {"start", "resume"} and (managed.runner is None or managed.runner.done()):
        managed.runner = asyncio.create_task(_run_clock(run_id, managed))
    if action in {"pause", "stop", "reset", "step"}:
        await _broadcast(managed, state)
    return state


def _validate_command_state(managed: ManagedRun, command: str, payload: dict[str, str | None]) -> str | None:
    sim = managed.simulation
    if command == "stop":
        if sim.status in {"completed", "stopped"}:
            return f"Cannot stop a mission in {sim.status} state."
    elif command == "reset":
        return None
    elif sim.status not in {"ready", "running", "paused"}:
        return "Threat-response commands require an active mission state."

    if command == "isolate_uav":
        uav = sim.state["uavs"].get(payload.get("uav_id") or "")
        if uav is None or not uav["alerted"] or uav["status"] == "isolated":
            return "Isolation requires an active validated telemetry alert."
    elif command == "reassign_task":
        task = sim.state["tasks"].get(payload.get("task_id") or "")
        target = sim.state["uavs"].get(payload.get("target_uav_id") or "")
        if task is None or task["status"] != "pending":
            return "Only an existing pending task can be reassigned."
        if target is None or target["status"] != "active":
            return "The target aircraft must exist and be active."
        if task["uav_id"] == target["id"]:
            return "The task is already assigned to that aircraft."
        rejection = reassignment_block_reason(sim.state, task["uav_id"] or "", task, target)
        if rejection:
            return f"The target aircraft is not eligible: {rejection}."
    return None


async def _execute_approved_command(
    decision: CommandDecisionRecord, reviewer: AuthContext, request: Request,
) -> dict:
    managed = _get(decision.run_id)
    payload = json.loads(decision.payload_json)
    async with managed.lock:
        validation_error = _validate_command_state(managed, decision.command, payload)
        if validation_error:
            raise HTTPException(status_code=409, detail=validation_error)
        sim = managed.simulation
        if decision.command in {"stop", "reset"}:
            if decision.command == "stop":
                sim.stop()
            else:
                sim.reset()
        elif decision.command == "isolate_uav":
            uav_id = payload["uav_id"]
            uav = sim.state["uavs"][uav_id]
            uav["status"] = "isolated"
            sim._emit(
                "uav_isolated", uav_id=uav_id, severity="high",
                reason=f"General review {decision.id} approved isolation of a validated alert.",
            )
            for event in reassign_tasks(sim.state, uav_id):
                event_type = event.pop("type")
                sim._emit(event_type, **event)
        else:
            task_id = payload["task_id"]
            target = sim.state["uavs"][payload["target_uav_id"]]
            task = sim.state["tasks"][task_id]
            source = sim.state["uavs"].get(task["uav_id"]) if task["uav_id"] else None
            task["uav_id"] = target["id"]
            if source is not None and source["task_id"] == task_id:
                source["task_id"] = next((
                    item["id"] for item in sim.state["tasks"].values()
                    if item["uav_id"] == source["id"] and item["status"] == "pending"
                ), None)
            if target["task_id"] is None:
                target["task_id"] = task_id
            sim._emit(
                "task_reassigned", uav_id=target["id"], task_id=task_id, severity="info",
                reason=f"General review {decision.id} approved task reassignment.",
            )
        state = await managed.persist()
    await _broadcast(managed, state)
    _record_operator_action(
        request, reviewer, f"command_{decision.command}_executed", "command_decision",
        decision.id, f"Approved command {decision.command} executed against run {decision.run_id}.",
    )
    return state


@app.post("/api/command-decisions", status_code=201)
async def create_command_decision(
    payload: CommandDecisionCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.COMMAND_REQUEST)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    managed = _require_run_owner(payload.run_id, context, request)
    command_payload = payload.model_dump(exclude={"run_id", "command"})
    async with managed.lock:
        validation_error = _validate_command_state(managed, payload.command, command_payload)
        if validation_error:
            raise HTTPException(status_code=409, detail=validation_error)
    decision_id = uuid.uuid4().hex
    decision = CommandDecisionRecord(
        id=decision_id, requester_id=context.user.id, run_id=payload.run_id,
        command=payload.command, payload_json=json.dumps(command_payload, sort_keys=True),
        status="pending",
    )
    with SessionLocal() as db:
        db.add(decision)
        db.commit()
    _record_operator_action(
        request, context, "command_decision_requested", "command_decision", decision_id,
        f"Requested approval for {payload.command} on run {payload.run_id}.",
    )
    return {"id": decision_id, "status": "pending", "command": payload.command, "run_id": payload.run_id}


@app.get("/api/command-decisions")
def list_command_decisions(
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    can_review = has_permission(
        context.user.role, Permission.COMMAND_APPROVE,
    )
    with SessionLocal() as db:
        query = select(CommandDecisionRecord).where(
            (CommandDecisionRecord.requester_id == context.user.id)
            | (CommandDecisionRecord.status == "pending" if can_review else False)
        ).order_by(CommandDecisionRecord.created_at.desc())
        records = db.scalars(query).all()
        requests = [{
            "id": row.id, "requester_id": row.requester_id,
            "requester_username": (
                db.get(UserRecord, row.requester_id).username
                if db.get(UserRecord, row.requester_id) else "UNKNOWN"
            ),
            "reviewer_id": row.reviewer_id, "run_id": row.run_id,
            "command": row.command, "payload": json.loads(row.payload_json),
            "status": row.status, "created_at": row.created_at.isoformat() + "Z",
            "decided_at": row.decided_at.isoformat() + "Z" if row.decided_at else None,
            "decision_reason": row.decision_reason,
        } for row in records]
    return {"requests": requests}


@app.post("/api/command-decisions/{decision_id}/decision")
async def review_command_decision(
    decision_id: str,
    payload: CommandDecisionReviewRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.COMMAND_APPROVE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    if payload.decision == "reject" and not (payload.reason and payload.reason.strip()):
        _record_operator_denial(
            request, context, "command_decision_validation", "command_decision",
            decision_id, "Rejection was submitted without a reason.",
        )
        raise HTTPException(status_code=422, detail="A reason is required to reject a command request.")
    with SessionLocal() as db:
        begin_write_transaction(db)
        decision = db.get(CommandDecisionRecord, decision_id)
        if decision is None:
            db.rollback()
            _record_operator_denial(
                request, context, "command_decision_review", "command_decision",
                decision_id, "Command decision request was not found.",
            )
            raise HTTPException(status_code=404, detail="Command decision request not found.")
        if decision.requester_id == context.user.id:
            db.rollback()
            _record_operator_denial(
                request, context, "command_decision_self_review", "command_decision",
                decision_id, "Command requesters cannot approve or reject their own requests.",
            )
            raise HTTPException(status_code=403, detail="Command requesters cannot approve or reject their own requests.")
        if decision.status != "pending":
            db.rollback()
            _record_operator_denial(
                request, context, "command_decision_review", "command_decision",
                decision_id, f"Command decision request is already {decision.status}.",
            )
            raise HTTPException(status_code=409, detail="This command request is no longer pending.")
        requester = db.get(UserRecord, decision.requester_id)
        if requester is None or requester.account_status != "APPROVED" or not has_permission(
            requester.role or "", Permission.COMMAND_REQUEST,
        ):
            decision.status = "failed"
            decision.reviewer_id = context.user.id
            decision.decided_at = utcnow_naive()
            decision.decision_reason = "Requesting account is inactive or no longer authorized for this command."
            db.commit()
            _record_operator_denial(
                request, context, "command_decision_requester_revoked", "command_decision",
                decision.id, decision.decision_reason,
            )
            raise HTTPException(status_code=409, detail="The requester is no longer active or authorized for this command.")
        decision.reviewer_id = context.user.id
        decision.decided_at = utcnow_naive()
        decision.decision_reason = payload.reason.strip() if payload.reason else None
        if payload.decision == "reject":
            decision.status = "rejected"
            db.commit()
            _record_operator_action(
                request, context, "command_decision_rejected", "command_decision",
                decision.id, decision.decision_reason or "Command request rejected.",
            )
            return {"id": decision.id, "status": decision.status}
        decision.status = "executing"
        db.commit()

    try:
        with SessionLocal() as db:
            decision = db.get(CommandDecisionRecord, decision_id)
            if decision is None:
                raise HTTPException(status_code=404, detail="Command decision request not found.")
            result = await _execute_approved_command(decision, context, request)
            decision_status = "approved"
    except HTTPException as exc:
        with SessionLocal() as db:
            decision = db.get(CommandDecisionRecord, decision_id)
            if decision is not None and decision.status == "executing":
                decision.status = "failed"
                decision.decision_reason = f"Execution failed: {exc.detail}"
                db.commit()
        _record_operator_denial(
            request, context, "command_decision_execution_failed", "command_decision",
            decision_id, str(exc.detail),
        )
        raise
    with SessionLocal() as db:
        decision = db.get(CommandDecisionRecord, decision_id)
        if decision is not None:
            decision.status = decision_status
            db.commit()
    _record_operator_action(
        request, context, "command_decision_approved", "command_decision",
        decision_id, payload.reason or "High-impact simulated command approved and executed.",
    )
    return {"id": decision_id, "status": decision_status, "state": result}


@app.post("/api/simulations/{run_id}/{action}")
async def control_simulation(
    run_id: str,
    action: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SIMULATION_CONTROL)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    if action in {"stop", "reset"} and context.user.role != "GENERAL":
        raise HTTPException(
            status_code=409,
            detail="Operators must submit this command for General review through /api/command-decisions.",
        )
    if action not in {"start", "pause", "resume", "step", "stop", "reset"}:
        _record_operator_denial(request, context, "mission_control", "run", run_id, "Unknown mission lifecycle action.")
        raise HTTPException(status_code=404, detail="Unknown simulation action")
    _require_run_owner(run_id, context, request)
    state = await _control(run_id, action, request, context)
    _record_operator_action(request, context, f"mission_{action}", "run", run_id, f"Operator requested simulation {action}")
    return state


@app.post("/api/simulations/{run_id}/alerts/{uav_id}/acknowledge")
async def acknowledge_alert(
    run_id: str,
    uav_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.THREAT_ACKNOWLEDGE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    managed = _require_run_owner(run_id, context, request)
    async with managed.lock:
        sim = managed.simulation
        uav = sim.state["uavs"].get(uav_id)
        if sim.status not in {"ready", "running", "paused"}:
            _record_operator_denial(request, context, "alert_acknowledged", "uav", uav_id, "Mission state does not allow alert acknowledgment.")
            raise HTTPException(status_code=409, detail="Alerts can only be acknowledged in an active mission state.")
        if uav is None or not uav["alerted"]:
            _record_operator_denial(request, context, "alert_acknowledged", "uav", uav_id, "No validated active alert exists.")
            raise HTTPException(status_code=409, detail="No validated active alert exists for that aircraft.")
        if uav["alert_acknowledged"]:
            _record_operator_denial(request, context, "alert_acknowledged", "uav", uav_id, "Validated alert was already acknowledged.")
            raise HTTPException(status_code=409, detail="The alert has already been acknowledged.")
        uav["alert_acknowledged"] = True
        sim._emit(
            "alert_acknowledged", uav_id=uav_id, severity=uav["threat_level"],
            reason="Authorized user acknowledged the validated telemetry alert.",
        )
        state = await managed.persist()
    await _broadcast(managed, state)
    _record_operator_action(request, context, "alert_acknowledged", "uav", uav_id, "Validated alert acknowledged")
    return {"state": state}


@app.post("/api/simulations/{run_id}/uavs/{uav_id}/isolate")
async def isolate_uav(
    run_id: str,
    uav_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.THREAT_ISOLATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    managed = _require_run_owner(run_id, context, request)
    async with managed.lock:
        validation_error = _validate_command_state(managed, "isolate_uav", {"uav_id": uav_id})
        if validation_error:
            raise HTTPException(status_code=409, detail=validation_error)
        uav = managed.simulation.state["uavs"][uav_id]
        uav["status"] = "isolated"
        managed.simulation._emit(
            "uav_isolated", uav_id=uav_id, severity="high",
            reason="General executed isolation after validating the telemetry alert.",
        )
        for event in reassign_tasks(managed.simulation.state, uav_id):
            event_type = event.pop("type")
            managed.simulation._emit(event_type, **event)
        state = await managed.persist()
    await _broadcast(managed, state)
    _record_operator_action(request, context, "uav_isolated", "uav", uav_id, "General isolated a validated alert.")
    return {"state": state}


@app.post("/api/simulations/{run_id}/tasks/{task_id}/reassign")
async def reassign_task(
    run_id: str,
    task_id: str,
    payload: TaskReassignmentRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.TASK_REASSIGN)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    managed = _require_run_owner(run_id, context, request)
    payload_dict = {"task_id": task_id, "target_uav_id": payload.target_uav_id}
    async with managed.lock:
        validation_error = _validate_command_state(managed, "reassign_task", payload_dict)
        if validation_error:
            raise HTTPException(status_code=409, detail=validation_error)
        sim = managed.simulation
        task = sim.state["tasks"][task_id]
        target = sim.state["uavs"][payload.target_uav_id]
        source = sim.state["uavs"].get(task["uav_id"]) if task["uav_id"] else None
        task["uav_id"] = target["id"]
        if source is not None and source["task_id"] == task_id:
            source["task_id"] = next((
                item["id"] for item in sim.state["tasks"].values()
                if item["uav_id"] == source["id"] and item["status"] == "pending"
            ), None)
        if target["task_id"] is None:
            target["task_id"] = task_id
        sim._emit(
            "task_reassigned", uav_id=target["id"], task_id=task_id, severity="info",
            reason="General reassigned a validated pending task.",
        )
        state = await managed.persist()
    await _broadcast(managed, state)
    _record_operator_action(request, context, "task_reassigned", "task", task_id, "General reassigned a pending task.")
    return {"state": state}


@app.websocket("/api/simulations/{run_id}/stream")
async def stream_simulation(websocket: WebSocket, run_id: str) -> None:
    try:
        context = get_auth_context(websocket)
    except HTTPException:
        await websocket.close(code=4401, reason="Authentication required")
        return
    managed = runs.get(run_id)
    if managed is None:
        await websocket.close(code=4404)
        return
    try:
        _require_run_owner(run_id, context, websocket)
    except HTTPException:
        await websocket.close(code=4404)
        return
    origin = websocket.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        await websocket.close(code=4403, reason="Origin not allowed")
        return
    await websocket.accept()
    client = StreamClient(websocket)
    managed.sockets.add(client)
    client.sender = asyncio.create_task(_stream_sender(client))
    client.outbound.put_nowait(managed.simulation.view())
    try:
        while True:
            await websocket.receive_text()  # Client pings keep the connection observable.
    except WebSocketDisconnect:
        pass
    finally:
        managed.sockets.discard(client)
        if client.sender is not None and not client.sender.done():
            client.sender.cancel()
            await asyncio.gather(client.sender, return_exceptions=True)


@app.websocket("/api/telemetry/px4/stream")
async def stream_px4_telemetry(websocket: WebSocket) -> None:
    try:
        context = get_auth_context(websocket)
    except HTTPException:
        await websocket.close(code=4401, reason="Authentication required")
        return
    if not has_permission(context.user.role, Permission.MISSION_READ_OWN):
        await websocket.close(code=4403, reason="Mission read permission required")
        return
    origin = websocket.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        await websocket.close(code=4403, reason="Origin not allowed")
        return
    if px4_udp_transport is None:
        await websocket.close(code=1013, reason="PX4 telemetry receiver is not enabled")
        return
    delta_trails = websocket.query_params.get("trail_mode") == "delta-v1"
    trail_cursor = websocket.query_params.get("since") if delta_trails else None
    await websocket.accept()
    try:
        while True:
            try:
                snapshot = (
                    px4_telemetry_store.stream_snapshot(since=trail_cursor)
                    if delta_trails else px4_telemetry_store.snapshot()
                )
                await asyncio.wait_for(websocket.send_json(snapshot), timeout=5.0)
                if delta_trails:
                    trail_cursor = snapshot["generated_at"]
            except asyncio.TimeoutError:
                await websocket.close(code=1013, reason="PX4 telemetry client is too slow")
                return
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass


@app.websocket("/api/telemetry/gazebo/stream")
async def stream_gazebo_pose(websocket: WebSocket) -> None:
    try:
        context = get_auth_context(websocket)
    except HTTPException:
        await websocket.close(code=4401, reason="Authentication required")
        return
    if not has_permission(context.user.role, Permission.MISSION_READ_OWN):
        await websocket.close(code=4403, reason="Mission read permission required")
        return
    origin = websocket.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        await websocket.close(code=4403, reason="Origin not allowed")
        return
    if gazebo_pose_udp_transport is None:
        await websocket.close(code=1013, reason="Gazebo pose receiver is not enabled")
        return
    delta_trails = websocket.query_params.get("trail_mode") == "delta-v1"
    trail_cursor = websocket.query_params.get("since") if delta_trails else None
    await websocket.accept()
    try:
        while True:
            try:
                snapshot = (
                    gazebo_pose_store.stream_snapshot(since=trail_cursor)
                    if delta_trails else gazebo_pose_store.snapshot()
                )
                await asyncio.wait_for(websocket.send_json(snapshot), timeout=5.0)
                if delta_trails:
                    trail_cursor = snapshot["generated_at"]
            except asyncio.TimeoutError:
                await websocket.close(code=1013, reason="Gazebo pose client is too slow")
                return
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass


@app.get("/api/history")
def history(context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    return {"runs": list_runs(context.user.id, include_all=include_all)}


@app.get("/api/replay/{run_id}")
def replay(run_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    result = get_replay(run_id, context.user.id, include_all=include_all)
    if result is None:
        raise HTTPException(status_code=404, detail="Recorded run not found")
    return result


def _experiment(config: SimulationConfig, policy: str) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    trial_id = uuid.uuid4().hex[:12]
    config.response_policy = policy
    sim = Simulation(config, detector_model=ACTIVE_ML_MODEL)
    sim.start()
    first_anomaly = None
    first_detected = None
    for _ in range(config.duration):
        view = sim.step()
        if first_anomaly is None and config.spoofing_type != "none" and sim.tick_count >= config.spoof_start:
            if any(event["type"] == "anomaly_detected" for event in sim.events):
                first_anomaly = config.spoof_start
                first_detected = sim.tick_count
        if sim.status == "completed":
            break
    events = sim.events
    detection_events = [e for e in events if e["type"] == "anomaly_detected"]
    true_positive = 1 if detection_events and config.spoofing_type != "none" else 0
    false_positive = 1 if detection_events and config.spoofing_type == "none" else 0
    false_negative = 1 if not detection_events and config.spoofing_type != "none" else 0
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    completed = view["progress"]["completed"]
    completed_at = datetime.now(timezone.utc)
    return {"policy": policy, "status": sim.status, "ticks": sim.tick_count, "tasks_completed": completed, "tasks_total": config.task_count,
            "tasks_remaining": config.task_count - completed, "completion_rate": completed / config.task_count,
            "continuity_score": completed / config.task_count, "continuity_loss": 1 - completed / config.task_count,
            "completion_time_ticks": sim.tick_count if completed == config.task_count else None,
            "mean_task_completion_tick": (sum(task["completed_tick"] for task in sim.state["tasks"].values() if task["completed_tick"] is not None) / completed) if completed else None,
            "detection_delay_ticks": (first_detected - first_anomaly) if first_detected is not None and first_anomaly is not None else None,
            "precision": precision, "recall": recall, "f1": f1, "false_positive_rate": false_positive,
            "isolated_uavs": len({e["uav_id"] for e in events if e["type"] == "uav_isolated"}),
            "unnecessary_isolation_rate": (sum(e["type"] == "uav_isolated" and (config.spoofing_type == "none" or e["uav_id"] != config.target_uav) for e in events) / max(1, sum(e["type"] == "uav_isolated" for e in events))),
            "reassigned_tasks": sum(e["type"] == "task_reassigned" for e in events),
            "unassigned_tasks": sum(e["type"] == "task_unassigned" for e in events),
            "events": events,
            "execution": {
                "trial_id": trial_id,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
            }}


@app.post("/api/experiments/compare")
def compare_experiment(
    request: ExperimentRequest,
    http_request: Request,
    context: AuthContext = Depends(require_permission(Permission.EXPERIMENT_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        base = SimulationConfig(**request.model_dump())
        experiment_started_at = datetime.now(timezone.utc)
        experiment_id = uuid.uuid4().hex[:12]
        baseline = _experiment(SimulationConfig(**asdict(base)), "baseline")
        adaptive = _experiment(SimulationConfig(**asdict(base)), "adaptive")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    experiment_completed_at = datetime.now(timezone.utc)
    config = asdict(base)
    config_hashes = {}
    for policy in ("baseline", "adaptive"):
        effective_config = {**config, "response_policy": policy}
        canonical_config = json.dumps(
            {
                "simulation_version": SIMULATION_VERSION,
                "detector_version": DETECTOR_VERSION,
                "detector_mode": "trained_ml" if ACTIVE_ML_MODEL is not None else "heuristic",
                "model_id": ACTIVE_ML_MODEL["model_id"] if ACTIVE_ML_MODEL is not None else None,
                "config": effective_config,
            },
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        config_hashes[policy] = hashlib.sha256(canonical_config.encode("utf-8")).hexdigest()
    metadata = {
        "metadata_schema_version": 3,
        "results_schema_version": 3,
        "experiment_id": experiment_id,
        "started_at": experiment_started_at.isoformat(),
        "completed_at": experiment_completed_at.isoformat(),
        "simulation_backend": "deterministic_python",
        "simulation_version": SIMULATION_VERSION,
        "source_revision": None,
        "source_revision_status": "unavailable_in_checkout_git_metadata",
        "scenario_id": None,
        "scenario_version_id": None,
        "px4_version": None,
        "gazebo_version": None,
        "dataset_version": None,
        "model_version": ACTIVE_ML_MODEL["model_id"] if ACTIVE_ML_MODEL is not None else None,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "seed": base.seed,
        "configuration": config,
        "configuration_hashes": config_hashes,
        "uav_configuration": {
            "vehicle_model": "abstract_simulated_uav",
            "uav_count": base.uav_count,
            "coordinate_system": "abstract simulation units",
        },
        "attack_configuration": {
            "spoofing_type": base.spoofing_type,
            "target_uav": base.target_uav,
            "spoof_start_tick": base.spoof_start,
            "spoof_duration_ticks": base.spoof_duration,
            "spoof_magnitude": base.spoof_magnitude,
        },
        "detector_version": DETECTOR_VERSION,
        "detector_mode": "trained_ml" if ACTIVE_ML_MODEL is not None else "heuristic",
        "model_confidence_calibrated": False if ACTIVE_ML_MODEL is not None else None,
        "detector_threshold": base.detector_threshold,
        "detection_metrics_scope": "run_level_event_presence_not_timestep_classifier_metrics",
        "response_policy_pair": ["baseline", "adaptive"],
        "repeatability_scope": "same implementation, Python runtime, configuration, and seed; cross-platform tolerances are not characterized",
    }
    results = {"baseline": baseline, "adaptive": adaptive}
    save_experiment(experiment_id, metadata, results, context.user.id)
    add_audit_event(
        action="experiment_created", outcome="success", reason="Operator ran deterministic comparison",
        request_id=getattr(http_request.state, "request_id", "request-unavailable"),
        actor_id=context.user.id, actor_name=context.user.full_name,
        target_type="experiment", target_id=experiment_id,
    )
    return {"experiment_id": experiment_id, "metadata": metadata, **results}


@app.get("/api/experiments/{experiment_id}.csv")
def export_experiment_csv(
    experiment_id: str,
    context: AuthContext = Depends(require_permission(Permission.ANALYTICS_EXPORT)),
) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    record = get_experiment(experiment_id, context.user.id, include_all=include_all)
    if record is None:
        raise HTTPException(status_code=404, detail="Recorded experiment not found")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["policy", "tasks_completed", "tasks_total", "completion_rate", "continuity_score", "continuity_loss", "completion_time_ticks", "mean_task_completion_tick", "detection_delay_ticks", "precision", "recall", "f1", "false_positive_rate", "isolated_uavs", "unnecessary_isolation_rate", "reassigned_tasks", "unassigned_tasks"])
    for policy in ("baseline", "adaptive"):
        result = record["results"][policy]
        writer.writerow([policy] + [result.get(key) for key in ("tasks_completed", "tasks_total", "completion_rate", "continuity_score", "continuity_loss", "completion_time_ticks", "mean_task_completion_tick", "detection_delay_ticks", "precision", "recall", "f1", "false_positive_rate", "isolated_uavs", "unnecessary_isolation_rate", "reassigned_tasks", "unassigned_tasks")])
    return {"filename": f"experiment-{experiment_id}.csv", "content": output.getvalue(), "metadata": record["metadata"]}


@app.get("/api/experiments/{experiment_id}")
def get_experiment_result(
    experiment_id: str,
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    result = get_experiment(experiment_id, context.user.id, include_all=include_all)
    if result is None:
        raise HTTPException(status_code=404, detail="Recorded experiment not found")
    return result


@app.get("/api/analytics/{run_id}.csv")
def export_csv(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.ANALYTICS_EXPORT)),
) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    record = get_replay(run_id, context.user.id, include_all=include_all)
    if record is None:
        raise HTTPException(status_code=404, detail="Recorded run not found")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["tick", "uav_id", "truth_x", "truth_y", "gps_x", "gps_y", "status", "anomaly_score", "threat_level", "task_id"])
    for state in record["snapshots"]:
        for uid, uav in state["uavs"].items():
            writer.writerow([state["tick"], uid, uav["position"]["x"], uav["position"]["y"], uav["gps"]["x"], uav["gps"]["y"], uav["status"], uav["anomaly_score"], uav["threat_level"], uav["task_id"]])
    return {"filename": f"{run_id}.csv", "content": output.getvalue()}
