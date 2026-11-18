#!/usr/bin/env python3
"""Unit tests for contracts, tool registration, navigation transitions,
panoramas, typed coordinates, TODO evidence and place-memory retrieval.

Run: python tests/test_harness_package.py
"""
import importlib.util
import os
import sys
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load(module_name):
    from importlib import import_module
    modules = {'contracts': 'comm_harness.tools.contracts', 'geometry': 'platforms.geometry', 'panorama': 'platforms.hm3d_m3.navigation.panorama', 'registry': 'comm_harness.tools.registry', 'retrieval': 'comm_harness.memory.retrieval', 'runtime': 'comm_harness.tools.runtime', 'state': 'tasks.harness_vln.navigation_state', 'todo': 'comm_harness.memory.todo', 'trace': 'comm_harness.session.trace', 'world_graph': 'comm_harness.memory.world_graph'}
    return import_module(modules[module_name])


contracts = load("contracts")
geometry = load("geometry")
panorama_mod = load("panorama")
registry_mod = load("registry")
runtime_mod = load("runtime")
state_mod = load("state")
todo_mod = load("todo")
wg_mod = load("world_graph")
retrieval_mod = load("retrieval")

Phase = contracts.Phase
SideEffect = contracts.SideEffect
ToolCall = contracts.ToolCall
Receipt = contracts.Receipt
safe_value = contracts.safe_value

ImagePixel = geometry.ImagePixel
MapCell = geometry.MapCell
NormalizedPixel = geometry.NormalizedPixel
RelativeYaw = geometry.RelativeYaw
WorldPoint = geometry.WorldPoint

CANONICAL_YAWS = panorama_mod.CANONICAL_YAWS
DIRECTION_YAW = panorama_mod.DIRECTION_YAW
Panorama = panorama_mod.Panorama

ToolSpec = registry_mod.ToolSpec
ToolRegistry = registry_mod.ToolRegistry
from tasks.object_navigation import ObjectNavigationTask
from platforms.hm3d_m3.tools import PLATFORM_TOOLS

def default_registry():
    return ObjectNavigationTask(types.SimpleNamespace()).registry(PLATFORM_TOOLS)


HarnessRuntime = runtime_mod.HarnessRuntime

NavigationState = state_mod.NavigationState
NAVIGATE = state_mod.NAVIGATE
STOP = state_mod.STOP

TodoMemory = todo_mod.TodoMemory
PENDING = todo_mod.PENDING
ACTIVE = todo_mod.ACTIVE
COMPLETED = todo_mod.COMPLETED
BLOCKED = todo_mod.BLOCKED

WorldGraph = wg_mod.WorldGraph

recall = retrieval_mod.recall


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name + ((": " + detail) if detail and not cond else ""))


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------
def test_contracts():
    check("TERMINAL mutates world", SideEffect.TERMINAL.mutates_world)
    check("WORLD_MUTATING mutates world", SideEffect.WORLD_MUTATING.mutates_world)
    check("PLANNING does not mutate world", not SideEffect.PLANNING.mutates_world)
    check("READ_ONLY does not mutate world", not SideEffect.READ_ONLY.mutates_world)
    check("BOOKKEEPING does not mutate world", not SideEffect.BOOKKEEPING.mutates_world)
    check("READ_ONLY.read_only", SideEffect.READ_ONLY.read_only)

    # report_rounds.py parses these dicts; the key set is part of the contract.
    call = ToolCall(call_id="c1", name="stop", step=7, args={"a": 1}, read_only=False)
    d = call.to_dict()
    check("ToolCall.to_dict keys",
          set(d) == {"call_id", "name", "tool_step", "args", "read_only", "reason"},
          str(sorted(d)))
    check("ToolCall.to_dict values",
          d["name"] == "stop" and d["tool_step"] == 7 and d["read_only"] is False)

    receipt = Receipt(receipt_id="r1", call_id="c1", tool_name="stop",
                      step_before=7, step_after=8, mutation="stop")
    rd = receipt.to_dict()
    check("Receipt.to_dict keys",
          set(rd) == {"receipt_id", "call_id", "tool_name", "step_before",
                      "step_after", "mutation", "metadata"},
          str(sorted(rd)))
    check("Receipt round-trips call_id", rd["call_id"] == "c1")

    # safe_value has to survive whatever an unparsed model response contains.
    check("safe_value passes scalars", safe_value(3) == 3 and safe_value("x") == "x")
    check("safe_value stringifies objects", isinstance(safe_value(object()), str))
    check("safe_value recurses into containers",
          safe_value({"k": [1, {"j": 2}]}) == {"k": [1, {"j": 2}]})

    check("Phase covers the state machine",
          {p.value for p in Phase} ==
          {"OBSERVE", "PLAN", "GROUND", "VALIDATE", "EXECUTE", "VERIFY", "TERMINAL"},
          str(sorted(p.value for p in Phase)))


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_registry():
    reg = default_registry()

    # Every tool name the evaluator actually traces must resolve, or the
    # violation channel fills with noise and hides the real mismatches.
    traced = ['observe_panorama', 'plan_navigation', 'ground_target',
              'project_target', 'verify_stop', 'stop', 'move', 'turn', 'action']
    missing = [n for n in traced if not reg.has(n)]
    check("all traced tool names registered", not missing, str(missing))

    check("unregistered tool reported",
          [k for k, _ in reg.validate("obserev_panorama")] == ["unregistered_tool"])
    check("registered tool with no checks is clean", reg.validate("query_depth") == [])

    # observe_panorama is READ_ONLY but physically turns the robot, so tracing
    # it as a mutation is legal. This is the case that would otherwise fire on
    # every panorama turn.
    check("panorama traced as mutation is not a violation",
          reg.validate("observe_panorama", read_only=False) == [])
    check("planning tool traced as mutation is a violation",
          [k for k, _ in reg.validate("plan_navigation", read_only=False)]
          == ["side_effect_mismatch"])
    check("terminal tool traced read_only is a violation",
          [k for k, _ in reg.validate("stop", read_only=True)] == ["side_effect_mismatch"])
    check("mutating tool traced as mutation is clean",
          reg.validate("move", read_only=False) == [])

    # required_args is unused by the v1 set today; verify the mechanism anyway,
    # because the first tool to declare one must not fail open.
    local = ToolRegistry()
    local.register(ToolSpec('move_to_pixel', SideEffect.WORLD_MUTATING,
                            required_args=('pixel',)))
    check("missing required arg reported",
          [k for k, _ in local.validate('move_to_pixel', args={})] == ["missing_args"])
    check("supplied required arg accepted",
          local.validate('move_to_pixel', args={'pixel': (1, 2)}) == [])

    check("duplicate registration rejected", _raises(ValueError, lambda: local.register(
        ToolSpec('move_to_pixel', SideEffect.READ_ONLY))))

    check("stop requires stop evidence", reg.get('stop').requires_stop_evidence)
    check("panorama is step consuming", reg.get('observe_panorama').step_consuming)
    check("stop is the only terminal tool",
          reg.names_by_side_effect(SideEffect.TERMINAL) == ['stop'])


def _raises(exc_type, fn):
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# NavigationState
# ---------------------------------------------------------------------------
def _pursuing_target(**overrides):
    nav = NavigationState(
        going_to_stop=True,
        panorama_got=True,
        navigate_or_not=True,
        is_backtracking=True,
        backtracking_only=True,
        planned_action=STOP,
        target_map_x=10,
        target_map_y=20,
        target_set_step=5,
    )
    for k, v in overrides.items():
        setattr(nav, k, v)
    return nav


def test_navigation_state():
    fresh = NavigationState()
    check("fresh state has no target", not fresh.has_target)
    check("fresh state plans NAVIGATE", fresh.planned_action == NAVIGATE and not fresh.wants_stop)
    check("steps_since_target_set is None without a target",
          fresh.steps_since_target_set(9) is None)

    nav = NavigationState()
    nav.set_target(3, 4, step=11)
    check("set_target sets the target", nav.has_target and (nav.target_map_x, nav.target_map_y) == (3, 4))
    check("steps_since_target_set counts from target_set_step",
          nav.steps_since_target_set(20) == 9)
    check("wants_stop tracks the planner action", NavigationState(planned_action=STOP).wants_stop)

    # --- the shared five fields ---
    nav = _pursuing_target()
    nav.on_target_timeout()
    cleared = (not nav.panorama_got and not nav.navigate_or_not
               and nav.target_map_x is None and nav.target_map_y is None
               and nav.target_set_step is None and not nav.backtracking_only)
    check("timeout clears the five target-pursuit fields", cleared)

    # A timeout preserves backtracking and planner intent; arrival resets both.
    check("timeout preserves is_backtracking (legacy asymmetry)", nav.is_backtracking is True)
    check("timeout preserves planned_action (legacy asymmetry)", nav.planned_action == STOP)
    check("timeout preserves going_to_stop", nav.going_to_stop is True)

    nav = _pursuing_target()
    nav.on_target_reached()
    check("reached clears the five target-pursuit fields",
          not nav.panorama_got and not nav.navigate_or_not and not nav.has_target
          and nav.target_set_step is None and not nav.backtracking_only)
    check("reached clears is_backtracking", nav.is_backtracking is False)
    check("reached resets planned_action", nav.planned_action == NAVIGATE)
    check("reached preserves going_to_stop", nav.going_to_stop is True)

    nav = _pursuing_target()
    nav.on_stop_rejected()
    check("rejection clears the stop intent", nav.going_to_stop is False)
    check("rejection clears panorama_got", nav.panorama_got is False)
    check("rejection clears navigate_or_not", nav.navigate_or_not is False)
    check("rejection resets planned_action", nav.planned_action == NAVIGATE)
    check("rejection KEEPS the target", nav.has_target and nav.target_set_step == 5)
    check("rejection keeps backtracking flags",
          nav.is_backtracking is True and nav.backtracking_only is True)

    snap = _pursuing_target().snapshot()
    check("snapshot is JSON-friendly",
          all(isinstance(v, (bool, int, str, type(None))) for v in snap.values()), str(snap))
    check("snapshot reports has_target, not raw coords",
          'has_target' in snap and 'target_map_x' not in snap)


# ---------------------------------------------------------------------------
# Panorama
# ---------------------------------------------------------------------------
def _legacy_panorama():
    """Exactly what get_panorama returns: 12 x 30 deg, rotate by one, stride 3."""
    frames = [{'angle': t * 30 % 360, 'step': t, 'rgb': f"rgb{t}", 'depth': f"d{t}"}
              for t in range(1, 13)]
    frames = [frames[-1]] + frames[:-1]
    return frames[::3]


def test_panorama():
    frames = _legacy_panorama()
    check("panorama has four frames", len(frames) == 4, str(len(frames)))
    pano = Panorama(frames)
    check("frames are the four canonical yaws", pano.angles() == list(CANONICAL_YAWS),
          str(pano.angles()))

    # At 30 degrees per turn, 12 views and stride 3 agree with yaw // 90.
    check("legacy yaw//90 layout holds", pano.legacy_layout_ok())

    check("forward is frame 0", pano.index_for_direction('forward') == 0)
    check("left is frame 1", pano.index_for_direction('left') == 1)
    check("behind is frame 2", pano.index_for_direction('behind') == 2)
    check("right is frame 3", pano.index_for_direction('right') == 3)
    check("view_for_direction returns the frame dict",
          pano.view_for_direction('behind')['angle'] == 180)
    check("direction lookup is case insensitive",
          pano.index_for_direction('Left') == 1)

    # Verify the panorama index convention used by rollout.
    for direction, yaw in DIRECTION_YAW.items():
        check(f"legacy_index matches yaw//90 for {direction}",
              pano.legacy_index(yaw) == yaw // 90)

    check("negative yaw normalises", pano.index_for_yaw(-90) == 3)
    check("yaw wraps past 360", pano.index_for_yaw(450) == 1)

    check("Panorama.of wraps a list", isinstance(Panorama.of(frames), Panorama))
    check("Panorama.of rejects the episode-done dict",
          Panorama.of({'turn_direction': 'episode_done', 'episode_finished': True}) is None)
    check("Panorama.of tolerates None", Panorama.of(None) is None)

    # A malformed panorama must degrade to legacy indexing, not raise inside a
    # rollout.
    untagged = Panorama([{'rgb': i} for i in range(4)])
    check("untagged frames fall back to legacy index", untagged.index_for_yaw(180) == 2)
    check("wrong-length panorama fails the layout check",
          not Panorama(frames[:3]).legacy_layout_ok())


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def test_geometry():
    check("NormalizedPixel rejects out-of-range",
          _raises(ValueError, lambda: NormalizedPixel(1.5, 0.5)))
    check("NormalizedPixel accepts the closed unit square",
          NormalizedPixel(0.0, 1.0).as_tuple() == (0.0, 1.0))

    px = NormalizedPixel(0.5, 0.5).to_image(1000, 1000)
    check("normalized -> image pixel", px.as_tuple() == (500, 500), str(px.as_tuple()))
    check("image pixel carries its frame size", (px.width, px.height) == (1000, 1000))

    # The bug this type exists to prevent: a bbox measured on the VLM's
    # 1000x1000 canvas used directly as an index into a 480x640 depth frame.
    rescaled = ImagePixel(1000 - 1, 0, 1000, 1000).rescaled_to(640, 480)
    check("rescale maps the far corner to the far corner",
          rescaled.as_tuple() == (639, 0), str(rescaled.as_tuple()))
    check("rescale keeps the new frame size", (rescaled.width, rescaled.height) == (640, 480))
    check("round trip through normalized is stable",
          ImagePixel(320, 240, 640, 480).to_normalized().to_image(640, 480).as_tuple() == (320, 240))

    check("out-of-bounds pixel detected", not ImagePixel(700, 10, 640, 480).in_bounds)
    check("clamp pulls into bounds", ImagePixel(700, -5, 640, 480).clamped().as_tuple() == (639, 0))
    check("clamp leaves in-bounds pixels alone",
          ImagePixel(10, 10, 640, 480).clamped().as_tuple() == (10, 10))

    check("WorldPoint round-trips", WorldPoint.from_tuple((1.5, -2.5)).as_tuple() == (1.5, -2.5))

    cell = MapCell.from_tuple((5, 7), resolution_cm=5)
    check("MapCell round-trips", cell.as_tuple() == (5, 7) and cell.resolution_cm == 5)
    check("MapCell clamps high", MapCell(999, 999, 5).clamped((480, 480)).as_tuple() == (479, 479))
    check("MapCell clamps low", MapCell(-3, -1, 5).clamped((480, 480)).as_tuple() == (0, 0))

    check("yaw normalises negatives", RelativeYaw(-90).normalized == 270.0)
    check("yaw normalises past 360", RelativeYaw(450).normalized == 90.0)
    check("yaw quadrants on the canonical values",
          [RelativeYaw(y).quadrant() for y in CANONICAL_YAWS] == [0, 1, 2, 3])
    check("yaw rounds to the nearest view instead of truncating",
          RelativeYaw(80).quadrant() == 1)
    check("yaw wraps 350 to the forward view", RelativeYaw(350).quadrant() == 0)

    check("coordinate types are frozen",
          _raises(Exception, lambda: setattr(WorldPoint(0.0, 0.0), 'x', 1.0)))


# ---------------------------------------------------------------------------
# todo memory
# ---------------------------------------------------------------------------
def _plan(n=3, **kw):
    todo = TodoMemory(**kw)
    todo.load([{"content": f"step {i}"} for i in range(1, n + 1)])
    return todo


def test_todo():
    todo = _plan(3)
    check("todo ids are issued in order",
          [t["todo_id"] for t in todo.visible()] == ["todo-1", "todo-2", "todo-3"])
    check("todo items start pending",
          all(t["status"] == PENDING for t in todo.visible()))
    check("todo renders by id, not position", "[todo-2]" in todo.render())
    check("todo render carries no bare index", "[1]" not in todo.render())

    # Batched index updates must resolve against the list shown to the planner,
    # even when an earlier operation removes an item.
    todo = _plan(4)
    todo.apply([
        {"op": "remove", "index": 0},
        {"index": 2, "status": "completed", "result": "saw the kitchen"},
    ])
    check("batch remove+update hits the item the model meant (todo-3)",
          todo.get("todo-3")["status"] == COMPLETED,
          "todo-3 is " + todo.get("todo-3")["status"])
    check("batch remove+update did not touch its neighbour",
          todo.get("todo-4")["status"] == PENDING)
    check("removed item is tombstoned, not popped",
          todo.get("todo-1") is not None and todo.get("todo-1")["removed"])
    check("tombstoned item is hidden from the planner",
          [t["todo_id"] for t in todo.visible()] == ["todo-2", "todo-3", "todo-4"])

    # Same batch, addressed by id: the form the prompts now ask for.
    todo = _plan(4)
    todo.apply([
        {"op": "remove", "todo_id": "todo-1"},
        {"todo_id": "todo-3", "status": "completed", "result": "saw the kitchen"},
    ])
    check("id-addressed batch hits todo-3", todo.get("todo-3")["status"] == COMPLETED)

    # An id that is dead stays dead: never recycled onto a new item.
    todo = _plan(2)
    todo.apply([{"op": "remove", "todo_id": "todo-2"}])
    todo.apply([{"op": "add", "content": "a new sub-goal"}])
    check("a new item never reuses a tombstoned id",
          [t["todo_id"] for t in todo.visible()] == ["todo-1", "todo-3"])
    rep = todo.apply([{"todo_id": "todo-2", "status": "completed", "result": "x"}])
    check("update on a tombstoned id is rejected, not redirected",
          rep[0].get("rejected") == "todo_removed", str(rep))
    rep = todo.apply([{"todo_id": "todo-99", "status": "active"}])
    check("update on an unknown id is rejected",
          rep[0].get("rejected") == "unknown_todo_id", str(rep))

    # Index addressing is still honoured (old plans keep working) but is flagged,
    # which is how we find out whether the planner has switched.
    seen = []
    todo = _plan(2, on_event=lambda step, kind, msg, **f: seen.append(kind))
    todo.apply([{"index": 0, "status": "active"}])
    check("index addressing still works", todo.get("todo-1")["status"] == ACTIVE)
    check("index addressing raises a violation", "todo_index_addressing" in seen, str(seen))
    rep = todo.apply([{"index": 9, "status": "active"}])
    check("out-of-range index is rejected",
          rep[0].get("rejected") == "index_out_of_range", str(rep))

    # At most one ACTIVE.
    todo = _plan(3)
    todo.apply([{"todo_id": "todo-1", "status": "active"}])
    rep = todo.apply([{"todo_id": "todo-2", "status": "active"}])
    check("a second ACTIVE is refused", rep[0].get("rejected") == "multiple_active", str(rep))
    check("the first ACTIVE survives the refusal", todo.active()["todo_id"] == "todo-1")
    check("the refused item stays pending", todo.get("todo-2")["status"] == PENDING)
    todo.apply([{"todo_id": "todo-1", "status": "completed", "result": "done"}])
    todo.apply([{"todo_id": "todo-2", "status": "active"}])
    check("ACTIVE is grantable once the holder completes",
          todo.active()["todo_id"] == "todo-2")

    # BLOCKED needs a stated reason; the prompt calls the field `reason`.
    todo = _plan(2)
    rep = todo.apply([{"todo_id": "todo-1", "status": "blocked"}])
    check("BLOCKED without a reason is refused",
          rep[0].get("rejected") == "blocked_without_reason", str(rep))
    check("the refused item stays pending", todo.get("todo-1")["status"] == PENDING)
    todo.apply([{"todo_id": "todo-1", "status": "blocked", "reason": "door is locked"}])
    check("BLOCKED with `reason` is accepted", todo.get("todo-1")["status"] == BLOCKED)
    check("`reason` is stored where render() reads it",
          "door is locked" in todo.render())

    # Evidence discipline.
    todo = _plan(2)
    rep = todo.apply([{"todo_id": "todo-1", "status": "completed"}])
    check("COMPLETED with neither result nor evidence is refused",
          rep[0].get("rejected") == "no_result_and_no_evidence", str(rep))
    check("the refused completion does not stick", todo.get("todo-1")["status"] == PENDING)

    rep = todo.apply([{"todo_id": "todo-1", "status": "completed", "result": "prose only"}])
    check("prose-only completion is accepted in non-strict mode",
          todo.get("todo-1")["status"] == COMPLETED)
    check("prose-only completion is still counted as a shortfall",
          any(e.get("warned") == "evidence_missing" for e in rep), str(rep))

    # The shortfall has to reach the trace, not just last_report: it is the
    # number that decides whether strict mode can be switched on, and
    # last_report is overwritten by the next batch.
    seen = []
    todo = _plan(2, on_event=lambda step, kind, msg, **f: seen.append((kind, f.get("reason"))))
    todo.apply([{"todo_id": "todo-1", "status": "completed", "result": "prose only"}])
    check("an unbacked completion is traced as a warning",
          ("todo_update_warned", "evidence_missing") in seen, str(seen))
    seen.clear()
    todo.apply([{"todo_id": "todo-2", "status": "active"}])
    check("a clean update traces nothing", seen == [], str(seen))

    issued = {"ob-000001", "rc-000004"}
    todo = _plan(2, evidence_resolver=lambda e: e in issued)
    rep = todo.apply([{"todo_id": "todo-1", "status": "completed",
                       "result": "saw it", "evidence_ids": ["ob-000001"]}])
    check("a resolvable evidence id completes cleanly",
          todo.get("todo-1")["status"] == COMPLETED
          and not any(e.get("warned") for e in rep), str(rep))
    check("evidence is kept on the item",
          todo.get("todo-1")["evidence_ids"] == ["ob-000001"])
    rep = todo.apply([{"todo_id": "todo-2", "status": "completed",
                       "result": "saw it", "evidence_ids": ["ob-999999"]}])
    check("an unissued evidence id is warned about, not silently trusted",
          any(e.get("warned") == "evidence_unresolved" for e in rep), str(rep))
    check("non-strict mode still applies the completion",
          todo.get("todo-2")["status"] == COMPLETED)

    todo = _plan(2, evidence_resolver=lambda e: e in issued, strict_evidence=True)
    rep = todo.apply([{"todo_id": "todo-1", "status": "completed",
                       "result": "saw it", "evidence_ids": ["ob-999999"]}])
    check("strict mode refuses an unissued evidence id",
          rep[0].get("rejected") == "evidence_unresolved", str(rep))
    check("strict mode leaves the item pending", todo.get("todo-1")["status"] == PENDING)
    rep = todo.apply([{"todo_id": "todo-1", "status": "completed", "result": "prose"}])
    check("strict mode refuses prose-only completion",
          rep[0].get("rejected") == "evidence_missing", str(rep))
    todo.apply([{"todo_id": "todo-1", "status": "completed",
                 "result": "saw it", "evidence_ids": ["rc-000004"]}])
    check("strict mode accepts an issued id", todo.get("todo-1")["status"] == COMPLETED)

    # add / rewrite positioning.
    todo = _plan(3)
    todo.apply([{"op": "add", "content": "inserted", "after": "todo-1"}])
    check("`after` inserts directly behind the anchor",
          [t["todo_id"] for t in todo.visible()] == ["todo-1", "todo-4", "todo-2", "todo-3"])
    rep = todo.apply([{"op": "add", "content": "orphan", "after": "todo-77"}])
    check("an unknown `after` appends rather than dropping the item",
          todo.visible()[-1]["content"] == "orphan")
    check("an unknown `after` is a warning, not a rejection",
          rep[0].get("ok") and rep[0].get("warning") == "unknown_after_id", str(rep))
    todo.apply([{"op": "rewrite", "todo_id": "todo-2", "content": "refined"}])
    check("rewrite edits content in place", todo.get("todo-2")["content"] == "refined")
    todo.apply([{"todo_id": "todo-2", "status": "completed", "result": "done"}])
    rep = todo.apply([{"op": "rewrite", "todo_id": "todo-2", "content": "again"}])
    check("rewrite of a completed item is refused",
          rep[0].get("rejected") == "already_completed", str(rep))

    # Malformed batches must never abort a rollout.
    todo = _plan(2)
    rep = todo.apply(["not a dict", {"op": "teleport", "todo_id": "todo-1"}, {}])
    check("a malformed batch is reported, not raised", len(rep) == 3, str(rep))
    check("unknown ops are named", any(e.get("rejected") == "unknown_op" for e in rep))
    check("a targetless update is named", any(e.get("rejected") == "no_target" for e in rep))
    check("todo.apply tolerates a non-list", todo.apply(None) == [])
    check("the plan survives a malformed batch", len(todo.visible()) == 2)

    # Rejections are told to the planner rather than silently dropped.
    todo = _plan(2)
    todo.apply([{"todo_id": "todo-1", "status": "completed"}])
    fb = todo.rejection_feedback()
    check("rejection feedback names the item and the reason",
          "todo-1" in fb and "no_result_and_no_evidence" in fb, fb)
    todo.apply([{"todo_id": "todo-1", "status": "active"}])
    check("a clean batch produces no feedback", todo.rejection_feedback() == "")

    # Legacy markdown plans pass through untouched.
    todo = TodoMemory()
    check("an empty memory is not initialised", not todo.initialised)
    check("as_list is None before a plan is loaded", todo.as_list() is None)
    todo.load_legacy_text("1. go left\n2. go right")
    check("legacy text renders verbatim", todo.render() == "1. go left\n2. go right")
    rep = todo.apply([{"todo_id": "todo-1", "status": "completed", "result": "x"}])
    check("structured updates are refused against a legacy plan",
          rep[0].get("rejected") == "legacy_text_todo", str(rep))

    todo = _plan(3)
    todo.apply([{"op": "remove", "todo_id": "todo-3"},
                {"todo_id": "todo-1", "status": "active"}])
    snap = todo.snapshot()
    check("snapshot counts visible and removed separately",
          snap["visible"] == 2 and snap["removed"] == 1 and snap["total"] == 3, str(snap))
    check("snapshot names the active item", snap["active_id"] == "todo-1", str(snap))
    check("status aliases normalise",
          todo_mod.normalize_status("In Progress") == ACTIVE
          and todo_mod.normalize_status("done") == COMPLETED
          and todo_mod.normalize_status("nonsense") is None)


# ---------------------------------------------------------------------------
# todo evidence freshness (report-only)
# ---------------------------------------------------------------------------
def test_todo_evidence_freshness():
    """The observed failure: an item written at step 12 from the opening
    panorama, then closed in that same planning turn citing the very step-0
    frames the planner read in order to write it. `has_evidence` is set
    membership, so it cannot see this. Detection is report-only -- these checks
    pin BOTH that the note fires AND that no transition and no counter moves,
    because the whole reason for measuring first is cross-round comparability.
    """
    trace = []
    rt = HarnessRuntime(emit=lambda t, s, **f: trace.append(dict(f, event=t, step=s)))
    rt.start_episode("ep-fresh", step=0)
    pano = rt.observations(0, "panorama_view", 4)

    todo = TodoMemory()
    # exactly how vlnce_baselines/agent.py wires it
    todo.evidence_resolver = rt.has_evidence
    todo.evidence_step_resolver = rt.evidence_step
    todo.on_event = rt.violation
    todo.on_note = rt.note

    check("observation ids resolve to the step they were issued at",
          [rt.evidence_step(o) for o in pano] == [0, 0, 0, 0])
    check("an id that was never issued has no step",
          rt.evidence_step("ob-999999") is None and rt.evidence_step(7) is None)

    todo.load([{"content": "locate a chair"}, {"content": "walk to the doorway"},
               {"content": "approach the chair"}], turn=0, step=12)
    check("load records the step the plan was written at",
          [t["created_step"] for t in todo.visible()] == [12, 12, 12])

    notes = lambda: [r for r in trace if r["event"] == "HARNESS_NOTE"]

    rep = todo.apply([{"todo_id": "todo-1", "status": "completed",
                       "evidence_ids": [pano[3]]}], step=12)
    check("same-step close on panorama evidence is still ACCEPTED",
          rep[0].get("ok") and todo.get("todo-1")["status"] == COMPLETED, str(rep))
    check("same-step close on panorama evidence is reported",
          notes() and notes()[-1]["kind"] == "todo_evidence_stale")
    check("both freshness signals fire independently",
          notes()[-1]["reasons"] == ["closed_at_creation_step", "evidence_predates_item"],
          str(notes()[-1]["reasons"]))
    check("the note carries enough to audit it without the frames",
          notes()[-1]["created_step"] == 12 and notes()[-1]["closed_step"] == 12
          and notes()[-1]["evidence_steps"] == [0], str(notes()[-1]))

    # Closed much later, so no action-free signal -- but the evidence is still
    # only the panorama the item was written from.
    todo.apply([{"todo_id": "todo-2", "status": "completed",
                 "evidence_ids": [pano[0]]}], step=90)
    check("stale evidence is caught even when the close is late",
          notes()[-1]["reasons"] == ["evidence_predates_item"], str(notes()[-1]["reasons"]))

    # One id issued at/after created_step clears it: that id is new information.
    before = len(notes())
    fresh = rt.observation(120, "current_view")
    todo.apply([{"todo_id": "todo-3", "status": "completed",
                 "evidence_ids": [pano[1], fresh]}], step=120)
    check("a genuinely new observation is not reported",
          len(notes()) == before and todo.get("todo-3")["status"] == COMPLETED)

    check("nothing was refused", all(t["status"] == COMPLETED for t in todo.visible()))
    check("freshness reports do not count as violations",
          rt.state.violations == 0 and rt.summary()["violations"] == 0)
    check("freshness reports are not traced as violations",
          not [r for r in trace if r["event"] == "HARNESS_VIOLATION"])

    # Without the resolver wired (harness disabled) the id-based signal is
    # simply unavailable; it must not crash or invent a reason.
    plain = TodoMemory()
    plain.load([{"content": "x"}], step=5)
    rep = plain.apply([{"todo_id": "todo-1", "status": "completed",
                        "result": "done"}], step=9)
    check("freshness check is inert with no resolver and no sink", rep[0].get("ok"))

    # A resolver that raises must not take the episode down with it.
    boom = TodoMemory()
    boom.evidence_step_resolver = lambda e: (_ for _ in ()).throw(RuntimeError("x"))
    boom.evidence_resolver = lambda e: True
    boom.on_note = lambda *a, **k: None
    boom.load([{"content": "x"}], step=5)
    rep = boom.apply([{"todo_id": "todo-1", "status": "completed",
                       "evidence_ids": ["ob-000001"]}], step=9)
    check("a raising resolver does not break the transition", rep[0].get("ok"), str(rep))


# ---------------------------------------------------------------------------
# world graph
# ---------------------------------------------------------------------------
def test_world_graph():
    g = WorldGraph()
    a = g.observe_place((0.0, 0.0), step=1, observation_ids=["ob-1", "ob-2"])
    check("first observation creates a place", a.place_id == "place-1" and a.visit_count == 1)

    # Merging is what makes visit_count mean anything; without it the
    # repeat-failure signal item 3 depends on is identically zero.
    same = g.observe_place((0.4, 0.2), step=5)
    check("a nearby observation merges into the same place",
          same.place_id == "place-1" and len(g.places) == 1)
    check("merging bumps the visit count", same.visit_count == 2)
    check("merging keeps the first step and advances the last",
          same.first_seen_step == 1 and same.last_seen_step == 5)

    b = g.observe_place((3.0, 0.0), step=9)
    check("a distant observation creates a new place",
          b.place_id == "place-2" and len(g.places) == 2)
    check("traversal records adjacency both ways",
          g.neighbours("place-1") == ["place-2"] and g.neighbours("place-2") == ["place-1"])
    check("traversal records a NAVIGABLE_TO edge",
          g.edge_counts()[wg_mod.NAVIGABLE_TO] == 1, str(g.edge_counts()))
    g.observe_place((0.0, 0.0), step=12)
    check("walking back does not duplicate the edge",
          g.edge_counts()[wg_mod.NAVIGABLE_TO] == 1, str(g.edge_counts()))
    check("observation ids become OBSERVED_FROM edges",
          g.edge_counts()[wg_mod.OBSERVED_FROM] == 2, str(g.edge_counts()))
    check("observation ids are deduped on the place",
          g.places["place-1"].observation_ids == ["ob-1", "ob-2"])

    # link_from_current=False is for a place recorded without having walked
    # there (e.g. a teleport-style reset), which must not invent a route.
    g2 = WorldGraph()
    g2.observe_place((0.0, 0.0), step=0)
    g2.observe_place((9.0, 9.0), step=1, link_from_current=False)
    check("an unlinked place records no route", g2.edge_counts()[wg_mod.NAVIGABLE_TO] == 0)

    # Objects.
    g = WorldGraph()
    g.observe_place((0.0, 0.0), step=1)
    o1 = g.observe_object("sofa", step=2, world_xz=(1.0, 0.0), source="grounding")
    check("grounding creates an object", o1.object_id == "obj-1")
    check("the object is attached to the current place", o1.place_id == "place-1")
    check("attachment records a CONTAINS edge", g.edge_counts()[wg_mod.CONTAINS] == 1)
    o2 = g.observe_object("sofa", step=6, world_xz=(1.3, 0.2))
    check("the same object seen again merges",
          o2.object_id == "obj-1" and len(g.objects) == 1)
    check("re-observation bumps the count", o2.observation_count == 2)
    o3 = g.observe_object("sofa", step=7, world_xz=(6.0, 6.0))
    check("a far-away same-category object is a different object",
          o3.object_id == "obj-2" and len(g.objects) == 2)
    check("category matching ignores case",
          g.observe_object("SOFA", step=9, world_xz=(1.4, 0.2)).object_id == "obj-1")

    # grounding writes a fresh sentence every turn rather than a label, so position --
    # not wording -- is the identity. Two groundings inside same_point_radius_m
    # are the same object however they were phrased; without this rule every
    # object stays at observation_count 1 and the object set degenerates into a
    # log of sentences. The rejected phrasing is kept as an alias, so this
    # merge loses nothing even in the rare case of two objects at one point.
    g = WorldGraph()
    g.observe_place((0.0, 0.0), step=1)
    a = g.observe_object("the stainless steel stove in the kitchen", step=2,
                         world_xz=(2.0, 0.0))
    b = g.observe_object("stove", step=5, world_xz=(2.1, 0.05))
    check("a reworded grounding at the same point is the same object",
          b.object_id == a.object_id and len(g.objects) == 1)
    check("the rejected phrasing is kept as an alias", b.aliases == ["stove"])
    check("either phrasing then matches the object",
          g.observe_object("stove", step=7, world_xz=(2.6, 0.0)).object_id == a.object_id)
    check("aliases are not duplicated", b.aliases == ["stove"])
    far = g.observe_object("stove", step=8, world_xz=(9.0, 9.0))
    check("an alias does not merge across the radius", far.object_id != a.object_id)    # Failures: the record visited_targets.pop() used to destroy.
    g = WorldGraph()
    check("a failure before the first place has nothing to attach to",
          g.record_failure(wg_mod.FAIL_NO_PROGRESS, step=0) is None)
    p1 = g.observe_place((0.0, 0.0), step=1)
    rec = g.record_failure(wg_mod.FAIL_NO_PROGRESS, step=4, subgoal="find the sofa",
                           detail="target timeout")
    check("a failure is attached to the current place",
          rec is not None and rec.place_id == p1.place_id)
    check("the place keeps the failure", g.places["place-1"].failure_ids == [rec.failure_id])
    check("the place itself is not deleted", "place-1" in g.places)
    check("a failure records a FAILED_FOR_SUBGOAL edge",
          g.edge_counts()[wg_mod.FAILED_FOR_SUBGOAL] == 1)

    g.record_failure(wg_mod.FAIL_STOP_REJECTED, step=6, subgoal="find the sofa")
    g.record_failure(wg_mod.FAIL_DEAD_END, step=7, subgoal="find the tv")
    check("failure_count is scoped to one subgoal",
          g.failure_count("place-1", subgoal="find the sofa") == 2,
          str(g.failure_count("place-1", subgoal="find the sofa")))
    check("an unrelated subgoal does not inherit the penalty",
          g.failure_count("place-1", subgoal="find the tv") == 1)
    check("omitting the subgoal counts every failure at the place",
          g.failure_count("place-1") == 3)
    check("an unknown place has no failures", g.failure_count("place-404") == 0)

    n = g.expire_failures(subgoal="find the sofa")
    check("expiring returns how many it retired", n == 2, str(n))
    check("expired failures stop counting",
          g.failure_count("place-1", subgoal="find the sofa") == 0)
    check("expiring one subgoal leaves the others alone",
          g.failure_count("place-1", subgoal="find the tv") == 1)
    check("expired failures are still on the record, not deleted",
          g.failure_count("place-1", subgoal="find the sofa", applicable_only=False) == 2)
    check("expiring twice is a no-op", g.expire_failures(subgoal="find the sofa") == 0)

    st = g.stats()
    check("stats reports the graph size",
          st["n_places"] == 1 and st["n_failures"] == 3, str(st))
    check("stats breaks failures down by type",
          st["failures_by_type"][wg_mod.FAIL_NO_PROGRESS] == 1, str(st["failures_by_type"]))
    check("to_dict is JSON-shaped", isinstance(g.to_dict()["places"][0], dict))

    # Events are advisory: a broken listener must not take a rollout down.
    def _boom(*a, **k):
        raise RuntimeError("listener exploded")

    g = WorldGraph(on_event=_boom)
    g.observe_place((0.0, 0.0), step=1)
    check("a failing event listener does not propagate", len(g.places) == 1)

    events = []
    g = WorldGraph(on_event=lambda kind, step, **f: events.append(kind))
    g.observe_place((0.0, 0.0), step=1)
    g.observe_object("sofa", step=2)
    g.record_failure(wg_mod.FAIL_COLLISION, step=3)
    check("each write emits its event",
          events == ["WORLD_GRAPH_PLACE", "WORLD_GRAPH_OBJECT", "WORLD_GRAPH_FAILURE"],
          str(events))

    g.reset()
    check("reset clears the episode",
          not g.places and not g.objects and not g.failures
          and not g.edges and g.current_place_id is None)
    check("ids restart after reset",
          g.observe_place((5.0, 5.0), step=0).place_id == "place-1")


# ---------------------------------------------------------------------------
# retrieval (recall_place)
# ---------------------------------------------------------------------------
def _house():
    """Three rooms in a row, walked left to right, agent ends in the kitchen.

    place-1 living room -- place-2 kitchen -- place-3 bathroom (with a plant).
    """
    g = WorldGraph()
    g.observe_place((0.0, 0.0), step=1, observation_ids=['ob-000001', 'ob-000002'],
                    waypoint_id=0)
    g.annotate_place(semantic_summary='living room with a sofa and a television')
    g.observe_object('grey sofa', step=2, world_xz=(1.0, 0.0))
    g.observe_place((3.0, 0.0), step=10, observation_ids=['ob-000005'], waypoint_id=1)
    g.annotate_place(semantic_summary='kitchen with an island and a stove')
    g.observe_place((6.0, 0.0), step=20, observation_ids=['ob-000009'], waypoint_id=2)
    g.annotate_place(semantic_summary='bathroom with a shower and a potted plant')
    g.observe_place((3.0, 0.0), step=30)  # back to the kitchen: current place
    return g


def test_retrieval():
    g = _house()
    r = recall(g, 'find the potted plant', current_xz=(3.0, 0.0),
               max_backtrack_distance_m=6.0)
    ids = [p.place_id for p in r.places]
    check("relevance puts the room naming the target first",
          ids and ids[0] == 'place-3', str(ids))
    check("the place the agent is standing at is not recalled back to it",
          'place-2' not in ids, str(ids))
    check("recall is not degraded when places have text", not r.degraded)
    check("the readout names a backtrack id the planner can act on",
          'backtrack id 2' in r.text, r.text)
    check("the readout carries citable observation ids", 'ob-000009' in r.text)
    check("the readout states how many of how many places it recalled",
          f"recalled {len(r.places)} of 3" in r.text, r.text)

    # A place with only a position is not a memory: rendering it back would
    # spend tokens on something the planner can neither act on nor cite.
    g2 = WorldGraph()
    g2.observe_place((0.0, 0.0), step=0)
    g2.observe_place((5.0, 0.0), step=1)
    r2 = recall(g2, 'find the plant', current_xz=(5.0, 0.0))
    check("contentless places are not recalled", r2.places == [], str(r2.places))
    check("an empty recall renders nothing at all", r2.text == "", repr(r2.text))

    # Degradation has to be visible. "Retrieval quietly stopped matching" and
    # "retrieval does not help" must not be the same observation.
    g3 = WorldGraph()
    g3.observe_place((0.0, 0.0), step=0, observation_ids=['ob-000001'])
    g3.observe_place((5.0, 0.0), step=1, observation_ids=['ob-000002'])
    r3 = recall(g3, 'find the plant', current_xz=(5.0, 0.0))
    check("no place text degrades the ranking", r3.degraded, str(r3.summary()))
    check("degradation names its reason", r3.degraded_reason == 'no_place_text')
    check("a degraded readout says so rather than posing as relevance",
          'recency only' in r3.text, r3.text)
    check("a degraded recall still returns places", len(r3.places) == 1)
    r3b = recall(_house(), '', current_xz=(3.0, 0.0))
    check("an empty query degrades too", r3b.degraded_reason == 'empty_query',
          str(r3b.summary()))

    # Failure memory: the whole point of item 4. A branch that failed for THIS
    # sub-goal must fall in the ranking.
    g = _house()
    before = {p.place_id: p.score for p in recall(g, 'find the potted plant').places}
    g.record_failure(wg_mod.FAIL_NO_PROGRESS, step=21, subgoal='find the potted plant',
                     place_id='place-3', detail='target_timeout')
    after = recall(g, 'find the potted plant')
    scored = {p.place_id: p for p in after.places}
    check("a prior failure lowers the place's score",
          scored['place-3'].score < before['place-3'], str(scored['place-3'].score))
    check("the penalty is what lowered it", scored['place-3'].penalty == 0.5)
    check("the failed branch is named in the readout",
          'ALREADY FAILED' in after.text and 'NO_PROGRESS x1' in after.text, after.text)
    check("the readout says the reminder is not a restriction",
          'not a restriction' in after.text)

    # ... but only for that sub-goal. "Could not reach the plant from here" says
    # nothing about looking for the television.
    other = {p.place_id: p for p in recall(g, 'find the television').places}
    check("the penalty does not leak to an unrelated sub-goal",
          other['place-3'].penalty == 0.0, str(other['place-3'].penalty))

    # The cap is what keeps a reflection advisory rather than a ban.
    for i in range(8):
        g.record_failure(wg_mod.FAIL_DEAD_END, step=30 + i, subgoal='find the potted plant',
                         place_id='place-3')
    capped = {p.place_id: p for p in recall(g, 'find the potted plant').places}
    check("the penalty is capped at one unit of relevance",
          capped['place-3'].penalty == retrieval_mod.PENALTY_CAP,
          str(capped['place-3'].penalty))
    check("nine failures do not make a place unreachable",
          'place-3' in capped, str(list(capped)))
    check("a heavily failed place is still rendered, just ranked down",
          'place-3' in recall(g, 'find the potted plant').text)

    # Expiring a reflection returns the place to its unpenalised score.
    g.expire_failures(subgoal='find the potted plant')
    expired = {p.place_id: p for p in recall(g, 'find the potted plant').places}
    check("expired failures stop costing anything", expired['place-3'].penalty == 0.0)
    check("an expired failure is no longer advertised to the planner",
          'ALREADY FAILED' not in recall(g, 'find the potted plant').text)

    # Failures where the agent stands are reported separately: it is not a place
    # to recall, but "you already tried and were wrong here" is worth saying.
    g = _house()
    g.record_failure(wg_mod.FAIL_STOP_REJECTED, step=31, subgoal='find the potted plant',
                     detail='fresh_depth 0.72 m')
    r = recall(g, 'find the potted plant', current_xz=(3.0, 0.0))
    check("a failure at the current place is carried", len(r.current_failures) == 1)
    check("the current place's failure is rendered",
          'where you stand now' in r.text and 'STOP_REJECTED' in r.text, r.text)
    check("the failure detail is rendered", 'fresh_depth 0.72 m' in r.text)

    # Assembly shape: seeds, one hop, hard cap.
    g4 = WorldGraph(merge_radius_m=0.1)
    for i in range(12):
        g4.observe_place((float(i), 0.0), step=i, observation_ids=[f'ob-{i:06d}'],
                         waypoint_id=i)
        g4.annotate_place(semantic_summary=f'room {i} with a table and a chair')
    g4.observe_place((100.0, 100.0), step=99, link_from_current=False)  # stand elsewhere
    r4 = recall(g4, 'table', seeds=3, max_places=5)
    check("the recalled set never exceeds max_places", len(r4.places) <= 5, str(len(r4.places)))
    check("exactly `seeds` entries are seeds",
          sum(1 for p in r4.places if p.seed) == 3,
          str([(p.place_id, p.seed) for p in r4.places]))
    check("the readout is ordered by score",
          [p.score for p in r4.places] == sorted((p.score for p in r4.places), reverse=True))
    seed_ids = {p.place_id for p in r4.places if p.seed}
    expansion = {p.place_id for p in r4.places if not p.seed}
    check("expansion entries are topological neighbours of a seed",
          all(any(pid in g4.neighbours(s) for s in seed_ids) for pid in expansion),
          str((seed_ids, expansion)))

    # A hub the agent kept returning to: here the one-hop expansion offers more
    # candidates than the cap allows, which is what actually exercises the cap.
    hub = WorldGraph(merge_radius_m=0.1)
    hub.observe_place((0.0, 0.0), step=0, observation_ids=['ob-000001'])
    hub.annotate_place(semantic_summary='hallway junction')
    for i in range(1, 7):
        hub.observe_place((float(i), 5.0), step=i, observation_ids=[f'ob-{i:06d}'],
                          waypoint_id=i)
        hub.annotate_place(semantic_summary=f'room {i} with a table')
        hub.observe_place((0.0, 0.0), step=100 + i)   # walk back through the hub
    check("the hub really has more neighbours than the cap",
          len(hub.neighbours('place-1')) == 6, str(hub.neighbours('place-1')))
    r_hub = recall(hub, 'table', seeds=1, max_places=5)
    check("expansion is truncated to max_places", len(r_hub.places) == 5,
          str([p.place_id for p in r_hub.places]))

    # A waypoint id is printed only when the planner could actually name it.
    r5 = recall(g4, 'table', current_xz=(0.0, 0.0), max_backtrack_distance_m=2.0)
    for entry in r5.places:
        if entry.waypoint_id is not None:
            check(f"{entry.place_id} is within the backtrack radius",
                  entry.distance_m is not None and entry.distance_m <= 2.0,
                  str(entry.distance_m))
    check("a far place gets no backtrack id",
          all(p.waypoint_id is None for p in r5.places if (p.distance_m or 0) > 2.0))
    r6 = recall(g4, 'table', current_xz=(0.0, 0.0), max_backtrack_distance_m=1000.0,
                waypoint_ids_available=[0])
    check("only waypoints the action list offers are printed",
          all(p.waypoint_id in (None, 0) for p in r6.places),
          str([(p.place_id, p.waypoint_id) for p in r6.places]))

    # Tokenisation / scoring primitives.
    check("tokenize drops stop words and case",
          retrieval_mod.tokenize("Go TO the Kitchen") == ['kitchen'],
          str(retrieval_mod.tokenize("Go TO the Kitchen")))
    check("recency is 1.0 for the place just seen",
          retrieval_mod.recency(g.places['place-2'], g.turn) == 1.0)
    check("salience saturates rather than rewarding clutter",
          retrieval_mod.salience(g.places['place-1']) <= 1.0)

    # The trace summary is what the A/B reads; it must be JSON-shaped and small.
    s = recall(_house(), 'find the potted plant').summary()
    check("summary reports the recall size",
          s['n_recalled'] == 2 and s['n_places_total'] == 3, str(s))
    check("summary carries per-place scores", 'score' in s['places'][0], str(s['places'][0]))
    check("summary does not embed the readout text",
          'text' not in s and isinstance(s['n_chars'], int), str(sorted(s)))

    # An episode with no places at all must not raise (recall runs on turn 1).
    empty = recall(WorldGraph(), 'find the plant')
    check("recall on an empty graph is empty, not an error",
          empty.places == [] and empty.text == "")


def main():
    test_contracts()
    test_registry()
    test_navigation_state()
    test_panorama()
    test_geometry()
    test_todo()
    test_todo_evidence_freshness()
    test_world_graph()
    test_retrieval()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
