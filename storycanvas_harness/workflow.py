from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import WorkflowCompileError
from .schemas import CanvasPlan, CompiledWorkflow, PlannedReference, WorkflowNodeSummary
from .utils import canonical_json, sha256_json

NODE_INPUT = "StoryCanvasInput"
NODE_POLICY = "StoryCanvasExecutionPolicy"
NODE_DIRECTOR = "StoryCanvasDirector"
NODE_SHARED_ASSET = "StoryCanvasSharedVisualAsset"
NODE_REFERENCE_ASSET = "StoryCanvasReferenceAsset"
NODE_REFERENCE_PACK = "StoryCanvasReferencePack"
NODE_H3_PROMPT = "StoryCanvasH3PromptCompiler"
NODE_H3_VIDEO = "StoryCanvasMiniMaxH3API"
NODE_ASSEMBLE = "StoryCanvasStoryAssemble"
NODE_MANIFEST = "StoryCanvasRunManifest"


@dataclass
class _UiNode:
    node_id: int
    type: str
    title: str
    pos: list[float]
    size: list[float]
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    widgets_values: list[Any] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    def serialize(self, order: int) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "type": self.type,
            "pos": self.pos,
            "size": self.size,
            "flags": {},
            "order": order,
            "mode": 0,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "title": self.title,
            "properties": {"Node name for S&R": self.type, **self.properties},
            "widgets_values": self.widgets_values,
        }


class _UiGraph:
    def __init__(self) -> None:
        self.nodes: list[_UiNode] = []
        self.links: list[list[Any]] = []
        self.next_node_id = 1
        self.next_link_id = 1

    def add(
        self,
        node_type: str,
        title: str,
        pos: tuple[float, float],
        *,
        size: tuple[float, float] = (300, 180),
        inputs: list[tuple[str, str, bool]] | None = None,
        outputs: list[tuple[str, str]] | None = None,
        widgets: list[Any] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> _UiNode:
        node = _UiNode(
            node_id=self.next_node_id,
            type=node_type,
            title=title,
            pos=list(pos),
            size=list(size),
            inputs=[
                {
                    "name": name,
                    "type": slot_type,
                    **({"shape": 7} if optional else {}),
                    "link": None,
                }
                for name, slot_type, optional in (inputs or [])
            ],
            outputs=[
                {"name": name, "type": slot_type, "links": []}
                for name, slot_type in (outputs or [])
            ],
            widgets_values=widgets or [],
            properties=properties or {},
        )
        self.next_node_id += 1
        self.nodes.append(node)
        return node

    def connect(
        self,
        origin: _UiNode,
        origin_slot: int,
        target: _UiNode,
        target_slot: int,
        slot_type: str,
    ) -> int:
        link_id = self.next_link_id
        self.next_link_id += 1
        self.links.append(
            [link_id, origin.node_id, origin_slot, target.node_id, target_slot, slot_type]
        )
        origin.outputs[origin_slot]["links"].append(link_id)
        target.inputs[target_slot]["link"] = link_id
        return link_id

    def serialize(self) -> tuple[list[dict[str, Any]], list[list[Any]]]:
        return [node.serialize(index) for index, node in enumerate(self.nodes)], self.links


def _widget_input(name: str, slot_type: str) -> tuple[str, str, bool]:
    return name, slot_type, False


def _link_input(name: str, slot_type: str, optional: bool = False) -> tuple[str, str, bool]:
    return name, slot_type, optional


def _reference_external(reference: PlannedReference) -> bool:
    return reference.source_asset_id is not None


def _subgraph_link(
    links: list[dict[str, Any]],
    link_id: int,
    origin_id: int,
    origin_slot: int,
    target_id: int,
    target_slot: int,
    slot_type: str,
) -> int:
    links.append(
        {
            "id": link_id,
            "origin_id": origin_id,
            "origin_slot": origin_slot,
            "target_id": target_id,
            "target_slot": target_slot,
            "type": slot_type,
        }
    )
    return link_id + 1


def _internal_node(
    node_id: int,
    node_type: str,
    title: str,
    pos: tuple[float, float],
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    widgets: list[Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "pos": list(pos),
        "size": [300, max(120, 86 + 24 * len(inputs))],
        "flags": {},
        "order": node_id - 1,
        "mode": 0,
        "inputs": inputs,
        "outputs": outputs,
        "title": title,
        "properties": {"Node name for S&R": node_type},
        "widgets_values": widgets,
    }


def _subgraph_for_shot(plan: CanvasPlan, shot_index: int) -> dict[str, Any]:
    shot = plan.shots[shot_index]
    subgraph_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"storycanvas:{plan.plan_id}:{shot.shot_id}"))
    inputs: list[dict[str, Any]] = []
    input_lookup: dict[str, int] = {}
    for name, slot_type in (("plan", "SC_PLAN"), ("policy", "SC_POLICY")):
        input_lookup[name] = len(inputs)
        inputs.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{subgraph_id}:input:{name}")),
                "name": name,
                "type": slot_type,
                "linkIds": [],
                "pos": [0, 120 + 70 * len(inputs)],
            }
        )
    for reference in shot.references:
        if _reference_external(reference):
            name = f"ref_{reference.order}_{reference.role}"
            input_lookup[name] = len(inputs)
            inputs.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{subgraph_id}:input:{name}")),
                    "name": name,
                    "type": "SC_ASSET",
                    "linkIds": [],
                    "pos": [0, 120 + 70 * len(inputs)],
                }
            )

    outputs: list[dict[str, Any]] = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{subgraph_id}:output:keyframe")),
            "name": "keyframe",
            "type": "SC_ASSET",
            "linkIds": [],
            "pos": [1650, 200],
        },
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{subgraph_id}:output:video")),
            "name": "video",
            "type": "SC_VIDEO",
            "linkIds": [],
            "pos": [1650, 300],
        },
    ]
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    # Current ComfyUI frontends normalize subgraph definitions into one registry
    # while loading a workflow. IDs that are only locally unique still work, but
    # trigger duplicate-ID remapping warnings and make audit diffs noisy.
    # Reserve a deterministic range per shot so every definition is globally
    # unique without sacrificing stable compilation.
    id_base = (shot_index + 1) * 1000
    next_node = id_base + 1
    next_link = id_base + 1
    input_node_id = -(2 * shot_index + 1)
    output_node_id = -(2 * shot_index + 2)

    reference_sources: list[tuple[int, int]] = []
    for reference in shot.references:
        if _reference_external(reference):
            input_name = f"ref_{reference.order}_{reference.role}"
            reference_sources.append((input_node_id, input_lookup[input_name]))
            continue
        node_id = next_node
        next_node += 1
        node_inputs: list[dict[str, Any]] = [
            {"name": "plan", "type": "SC_PLAN", "link": next_link},
            {"name": "policy", "type": "SC_POLICY", "link": next_link + 1},
            {
                "name": "asset_id",
                "type": "STRING",
                "widget": {"name": "asset_id"},
                "link": None,
            },
            {"name": "shot_id", "type": "STRING", "widget": {"name": "shot_id"}, "link": None},
            {
                "name": "reference_id",
                "type": "STRING",
                "widget": {"name": "reference_id"},
                "link": None,
            },
        ]
        nodes.append(
            _internal_node(
                node_id,
                NODE_REFERENCE_ASSET,
                f"Reference {reference.order}: {reference.role}",
                (220, 90 + 190 * (reference.order - 1)),
                node_inputs,
                [{"name": "asset", "type": "SC_ASSET", "links": []}],
                ["", shot.shot_id, reference.reference_id],
            )
        )
        next_link = _subgraph_link(
            links, next_link, input_node_id, input_lookup["plan"], node_id, 0, "SC_PLAN"
        )
        inputs[input_lookup["plan"]]["linkIds"].append(next_link - 1)
        next_link = _subgraph_link(
            links, next_link, input_node_id, input_lookup["policy"], node_id, 1, "SC_POLICY"
        )
        inputs[input_lookup["policy"]]["linkIds"].append(next_link - 1)
        reference_sources.append((node_id, 0))

    dependency_pack_id = next_node
    next_node += 1
    dependency_inputs: list[dict[str, Any]] = [
        {
            "name": "reference_count",
            "type": "INT",
            "widget": {"name": "reference_count"},
            "link": None,
        },
        *[
            {"name": f"reference_{index}", "type": "SC_ASSET", "link": None}
            for index in range(1, len(reference_sources) + 1)
        ],
    ]
    nodes.append(
        _internal_node(
            dependency_pack_id,
            NODE_REFERENCE_PACK,
            "Ordered Canvas References",
            (610, 180),
            dependency_inputs,
            [{"name": "reference_pack", "type": "SC_REF_PACK", "links": []}],
            [len(reference_sources)],
        )
    )
    for reference_index, (origin_id, origin_slot) in enumerate(reference_sources):
        target_slot = reference_index + 1
        next_link = _subgraph_link(
            links, next_link, origin_id, origin_slot, dependency_pack_id, target_slot, "SC_ASSET"
        )
        dependency_inputs[target_slot]["link"] = next_link - 1
        if origin_id == input_node_id:
            matching_input = next(
                item for item in inputs if input_lookup[item["name"]] == origin_slot
            )
            matching_input["linkIds"].append(next_link - 1)
        else:
            source_node = next(node for node in nodes if node["id"] == origin_id)
            source_node["outputs"][origin_slot]["links"].append(next_link - 1)

    keyframe_id = next_node
    next_node += 1
    keyframe_inputs: list[dict[str, Any]] = [
        {"name": "plan", "type": "SC_PLAN", "link": next_link},
        {"name": "policy", "type": "SC_POLICY", "link": next_link + 1},
        {"name": "asset_id", "type": "STRING", "widget": {"name": "asset_id"}, "link": None},
        {"name": "shot_id", "type": "STRING", "widget": {"name": "shot_id"}, "link": None},
        {
            "name": "reference_id",
            "type": "STRING",
            "widget": {"name": "reference_id"},
            "link": None,
        },
        {
            "name": "reference_pack",
            "type": "SC_REF_PACK",
            "shape": 7,
            "link": next_link + 2 if reference_sources else None,
        },
    ]
    nodes.append(
        _internal_node(
            keyframe_id,
            NODE_REFERENCE_ASSET,
            "Generate Final Canvas Keyframe",
            (960, 150),
            keyframe_inputs,
            [{"name": "asset", "type": "SC_ASSET", "links": []}],
            [shot.keyframe_asset_id, shot.shot_id, ""],
        )
    )
    next_link = _subgraph_link(
        links, next_link, input_node_id, input_lookup["plan"], keyframe_id, 0, "SC_PLAN"
    )
    inputs[input_lookup["plan"]]["linkIds"].append(next_link - 1)
    next_link = _subgraph_link(
        links, next_link, input_node_id, input_lookup["policy"], keyframe_id, 1, "SC_POLICY"
    )
    inputs[input_lookup["policy"]]["linkIds"].append(next_link - 1)
    if reference_sources:
        next_link = _subgraph_link(
            links, next_link, dependency_pack_id, 0, keyframe_id, 5, "SC_REF_PACK"
        )
        nodes[-2]["outputs"][0]["links"].append(next_link - 1)

    video_pack_id = next_node
    next_node += 1
    video_source_count = 1 + len(reference_sources)
    video_pack_inputs: list[dict[str, Any]] = [
        {
            "name": "reference_count",
            "type": "INT",
            "widget": {"name": "reference_count"},
            "link": None,
        },
        *[
            {"name": f"reference_{index}", "type": "SC_ASSET", "link": None}
            for index in range(1, video_source_count + 1)
        ],
    ]
    nodes.append(
        _internal_node(
            video_pack_id,
            NODE_REFERENCE_PACK,
            "H3 Ordered References (Canvas First)",
            (1260, 80),
            video_pack_inputs,
            [{"name": "reference_pack", "type": "SC_REF_PACK", "links": []}],
            [video_source_count],
        )
    )
    next_link = _subgraph_link(links, next_link, keyframe_id, 0, video_pack_id, 1, "SC_ASSET")
    nodes[-2]["outputs"][0]["links"].append(next_link - 1)
    video_pack_inputs[1]["link"] = next_link - 1
    for index, (origin_id, origin_slot) in enumerate(reference_sources, start=2):
        next_link = _subgraph_link(
            links, next_link, origin_id, origin_slot, video_pack_id, index, "SC_ASSET"
        )
        video_pack_inputs[index]["link"] = next_link - 1
        if origin_id == input_node_id:
            matching_input = next(
                item for item in inputs if input_lookup[item["name"]] == origin_slot
            )
            matching_input["linkIds"].append(next_link - 1)
        else:
            source_node = next(node for node in nodes if node["id"] == origin_id)
            source_node["outputs"][origin_slot]["links"].append(next_link - 1)

    prompt_id = next_node
    next_node += 1
    prompt_inputs: list[dict[str, Any]] = [
        {"name": "plan", "type": "SC_PLAN", "link": next_link},
        {"name": "reference_pack", "type": "SC_REF_PACK", "link": next_link + 1},
        {"name": "shot_id", "type": "STRING", "widget": {"name": "shot_id"}, "link": None},
    ]
    nodes.append(
        _internal_node(
            prompt_id,
            NODE_H3_PROMPT,
            "Compile Six-Part H3 Prompt",
            (1580, 80),
            prompt_inputs,
            [{"name": "h3_prompt", "type": "SC_H3_PROMPT", "links": []}],
            [shot.shot_id],
        )
    )
    next_link = _subgraph_link(
        links, next_link, input_node_id, input_lookup["plan"], prompt_id, 0, "SC_PLAN"
    )
    inputs[input_lookup["plan"]]["linkIds"].append(next_link - 1)
    next_link = _subgraph_link(links, next_link, video_pack_id, 0, prompt_id, 1, "SC_REF_PACK")
    nodes[-2]["outputs"][0]["links"].append(next_link - 1)

    video_id = next_node
    video_inputs: list[dict[str, Any]] = [
        {"name": "plan", "type": "SC_PLAN", "link": next_link},
        {"name": "h3_prompt", "type": "SC_H3_PROMPT", "link": next_link + 1},
        {"name": "reference_pack", "type": "SC_REF_PACK", "link": next_link + 2},
        {"name": "policy", "type": "SC_POLICY", "link": next_link + 3},
        {"name": "shot_id", "type": "STRING", "widget": {"name": "shot_id"}, "link": None},
    ]
    nodes.append(
        _internal_node(
            video_id,
            NODE_H3_VIDEO,
            "MiniMax-H3 Video (Paid Gate)",
            (1900, 130),
            video_inputs,
            [{"name": "video", "type": "SC_VIDEO", "links": []}],
            [shot.shot_id],
        )
    )
    next_link = _subgraph_link(
        links, next_link, input_node_id, input_lookup["plan"], video_id, 0, "SC_PLAN"
    )
    inputs[input_lookup["plan"]]["linkIds"].append(next_link - 1)
    next_link = _subgraph_link(links, next_link, prompt_id, 0, video_id, 1, "SC_H3_PROMPT")
    nodes[-2]["outputs"][0]["links"].append(next_link - 1)
    next_link = _subgraph_link(links, next_link, video_pack_id, 0, video_id, 2, "SC_REF_PACK")
    nodes[-3]["outputs"][0]["links"].append(next_link - 1)
    next_link = _subgraph_link(
        links, next_link, input_node_id, input_lookup["policy"], video_id, 3, "SC_POLICY"
    )
    inputs[input_lookup["policy"]]["linkIds"].append(next_link - 1)

    next_link = _subgraph_link(links, next_link, keyframe_id, 0, output_node_id, 0, "SC_ASSET")
    outputs[0]["linkIds"].append(next_link - 1)
    next(node for node in nodes if node["id"] == keyframe_id)["outputs"][0]["links"].append(
        next_link - 1
    )
    next_link = _subgraph_link(links, next_link, video_id, 0, output_node_id, 1, "SC_VIDEO")
    outputs[1]["linkIds"].append(next_link - 1)
    nodes[-1]["outputs"][0]["links"].append(next_link - 1)

    return {
        "id": subgraph_id,
        "version": 1,
        "state": {
            "lastGroupId": 0,
            "lastNodeId": next_node,
            "lastLinkId": next_link - 1,
            "lastRerouteId": 0,
        },
        "revision": 0,
        "config": {},
        "name": f"Shot {shot.order:02d} · {shot.title or shot.shot_id}",
        "inputNode": {"id": input_node_id, "bounding": [0, 80, 140, 160]},
        "outputNode": {"id": output_node_id, "bounding": [2250, 160, 140, 120]},
        "inputs": inputs,
        "outputs": outputs,
        "widgets": [],
        "nodes": nodes,
        "groups": [],
        "links": links,
        "extra": {"workflowRendererVersion": "LG", "storycanvas_shot_id": shot.shot_id},
        "category": "StoryCanvas/Shots",
        "description": f"Generated auditable Canvas-to-H3 workflow for {shot.shot_id}.",
    }


def _api_node(class_type: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs}


def _compile_api(
    plan: CanvasPlan, policy_json: str
) -> tuple[dict[str, Any], list[WorkflowNodeSummary]]:
    api: dict[str, Any] = {}
    index: list[WorkflowNodeSummary] = []
    counter = 1

    def add(class_type: str, title: str, inputs: dict[str, Any], shot_id: str | None = None) -> str:
        nonlocal counter
        node_id = str(counter)
        counter += 1
        api[node_id] = _api_node(class_type, inputs)
        index.append(
            WorkflowNodeSummary(
                node_id=node_id, class_type=class_type, title=title, shot_id=shot_id
            )
        )
        return node_id

    input_node = add(
        NODE_INPUT,
        "Story / Shot Input",
        {"input_kind": plan.input_kind, "payload_json": canonical_json(plan.source_input)},
    )
    policy_node = add(
        NODE_POLICY,
        "Execution Policy",
        {"policy_json": policy_json},
    )
    director_node = add(
        NODE_DIRECTOR,
        "Typed Director Plan",
        {
            "story_input": [input_node, 0],
            "policy": [policy_node, 0],
            "provider": "precomputed",
            "precomputed_plan_json": canonical_json(plan),
        },
    )
    asset_nodes: dict[str, str] = {}
    for asset in plan.shared_assets:
        node_id = add(
            NODE_SHARED_ASSET,
            f"Shared {asset.kind}: {asset.role}",
            {
                "plan": [director_node, 0],
                "policy": [policy_node, 0],
                "asset_id": asset.asset_id,
            },
        )
        asset_nodes[asset.asset_id] = node_id

    shot_video_nodes: list[str] = []
    for shot in plan.shots:
        dependency_assets: list[str] = []
        for reference in shot.references:
            if reference.source_asset_id:
                if reference.source_asset_id not in asset_nodes:
                    raise WorkflowCompileError(
                        f"No compiled asset node for {reference.source_asset_id} used by {shot.shot_id}"
                    )
                dependency_assets.append(asset_nodes[reference.source_asset_id])
            else:
                dependency_assets.append(
                    add(
                        NODE_REFERENCE_ASSET,
                        f"{shot.shot_id} reference {reference.order}",
                        {
                            "plan": [director_node, 0],
                            "policy": [policy_node, 0],
                            "asset_id": "",
                            "shot_id": shot.shot_id,
                            "reference_id": reference.reference_id,
                        },
                        shot.shot_id,
                    )
                )
        dependency_pack_inputs: dict[str, Any] = {"reference_count": len(dependency_assets)}
        for ref_index, node_id in enumerate(dependency_assets, start=1):
            dependency_pack_inputs[f"reference_{ref_index}"] = [node_id, 0]
        dependency_pack = add(
            NODE_REFERENCE_PACK,
            f"{shot.shot_id} Canvas references",
            dependency_pack_inputs,
            shot.shot_id,
        )
        keyframe = add(
            NODE_REFERENCE_ASSET,
            f"{shot.shot_id} final Canvas",
            {
                "plan": [director_node, 0],
                "policy": [policy_node, 0],
                "reference_pack": [dependency_pack, 0],
                "asset_id": shot.keyframe_asset_id,
                "shot_id": shot.shot_id,
                "reference_id": "",
            },
            shot.shot_id,
        )
        asset_nodes[shot.keyframe_asset_id] = keyframe
        video_pack_inputs: dict[str, Any] = {"reference_count": 1 + len(dependency_assets)}
        video_pack_inputs["reference_1"] = [keyframe, 0]
        for ref_index, node_id in enumerate(dependency_assets, start=2):
            video_pack_inputs[f"reference_{ref_index}"] = [node_id, 0]
        video_pack = add(
            NODE_REFERENCE_PACK,
            f"{shot.shot_id} H3 references",
            video_pack_inputs,
            shot.shot_id,
        )
        prompt_node = add(
            NODE_H3_PROMPT,
            f"{shot.shot_id} H3 prompt",
            {
                "plan": [director_node, 0],
                "reference_pack": [video_pack, 0],
                "shot_id": shot.shot_id,
            },
            shot.shot_id,
        )
        video_node = add(
            NODE_H3_VIDEO,
            f"{shot.shot_id} MiniMax-H3",
            {
                "plan": [director_node, 0],
                "h3_prompt": [prompt_node, 0],
                "reference_pack": [video_pack, 0],
                "policy": [policy_node, 0],
                "shot_id": shot.shot_id,
            },
            shot.shot_id,
        )
        shot_video_nodes.append(video_node)

    assemble_inputs: dict[str, Any] = {
        "plan": [director_node, 0],
        "policy": [policy_node, 0],
        "video_count": len(shot_video_nodes),
    }
    for video_index, node_id in enumerate(shot_video_nodes, start=1):
        assemble_inputs[f"video_{video_index}"] = [node_id, 0]
    assemble = add(NODE_ASSEMBLE, "Assemble Story", assemble_inputs)
    add(
        NODE_MANIFEST,
        "Write Run Manifest",
        {
            "plan": [director_node, 0],
            "policy": [policy_node, 0],
            "story_video": [assemble, 0],
        },
    )
    return api, index


def compile_workflow(plan: CanvasPlan, policy: Any) -> CompiledWorkflow:
    plan = CanvasPlan.model_validate(plan.model_dump(mode="json"))
    if len(plan.shots) > 24:
        raise WorkflowCompileError("The v1 ComfyUI compiler supports at most 24 shots per workflow")
    for asset in plan.shared_assets:
        if asset.dependencies or asset.references:
            raise WorkflowCompileError(
                f"Shared asset {asset.asset_id} has dependencies/references that the v1 ComfyUI compiler cannot wire; use independent shared assets or a custom canvas.render plugin"
            )
    for shot in plan.shots:
        wired = {ref.source_asset_id for ref in shot.references if ref.source_asset_id}
        if set(shot.dependencies) - wired:
            raise WorkflowCompileError(
                f"Shot {shot.shot_id} has dependencies without a reference edge; the v1 ComfyUI compiler requires explicit references"
            )
    policy_json = canonical_json(policy)
    graph = _UiGraph()
    input_node = graph.add(
        NODE_INPUT,
        "StoryCanvas Input",
        (80, 120),
        inputs=[_widget_input("input_kind", "STRING"), _widget_input("payload_json", "STRING")],
        outputs=[("story_input", "SC_INPUT")],
        widgets=[plan.input_kind, canonical_json(plan.source_input)],
    )
    policy_node = graph.add(
        NODE_POLICY,
        "Execution Policy · Preview Before Spend",
        (80, 420),
        inputs=[_widget_input("policy_json", "STRING")],
        outputs=[("policy", "SC_POLICY")],
        widgets=[policy_json],
    )
    director_node = graph.add(
        NODE_DIRECTOR,
        "Typed StoryCanvas Director",
        (470, 180),
        inputs=[
            _link_input("story_input", "SC_INPUT"),
            _link_input("policy", "SC_POLICY"),
            _widget_input("provider", "STRING"),
            _widget_input("precomputed_plan_json", "STRING"),
        ],
        outputs=[("plan", "SC_PLAN")],
        widgets=["precomputed", canonical_json(plan)],
        size=(380, 300),
    )
    graph.connect(input_node, 0, director_node, 0, "SC_INPUT")
    graph.connect(policy_node, 0, director_node, 1, "SC_POLICY")

    shared_nodes: dict[str, _UiNode] = {}
    shared_y = 40
    for asset in plan.shared_assets:
        node = graph.add(
            NODE_SHARED_ASSET,
            f"{asset.kind.title()} · {asset.role}",
            (950, shared_y),
            inputs=[
                _link_input("plan", "SC_PLAN"),
                _link_input("policy", "SC_POLICY"),
                _widget_input("asset_id", "STRING"),
            ],
            outputs=[("asset", "SC_ASSET")],
            widgets=[asset.asset_id],
        )
        shared_y += 250
        graph.connect(director_node, 0, node, 0, "SC_PLAN")
        graph.connect(policy_node, 0, node, 1, "SC_POLICY")
        shared_nodes[asset.asset_id] = node

    subgraphs = []
    shot_nodes: dict[str, _UiNode] = {}
    shot_video_nodes: list[_UiNode] = []
    x = 1420
    for shot_index, shot in enumerate(plan.shots):
        definition = _subgraph_for_shot(plan, shot_index)
        subgraphs.append(definition)
        instance = graph.add(
            definition["id"],
            definition["name"],
            (x + shot_index * 360, 250),
            size=(320, 160 + 26 * len(definition["inputs"])),
            inputs=[_link_input(item["name"], item["type"]) for item in definition["inputs"]],
            outputs=[(item["name"], item["type"]) for item in definition["outputs"]],
            properties={"storycanvas_shot_id": shot.shot_id},
        )
        graph.connect(director_node, 0, instance, 0, "SC_PLAN")
        graph.connect(policy_node, 0, instance, 1, "SC_POLICY")
        target_slot = 2
        for reference in shot.references:
            if not reference.source_asset_id:
                continue
            source = shared_nodes.get(reference.source_asset_id) or shot_nodes.get(
                reference.source_asset_id
            )
            if source is None:
                raise WorkflowCompileError(
                    f"Cannot resolve {reference.source_asset_id} for {shot.shot_id}"
                )
            graph.connect(source, 0, instance, target_slot, "SC_ASSET")
            target_slot += 1
        shot_nodes[shot.keyframe_asset_id] = instance
        shot_video_nodes.append(instance)

    assemble_x = x + len(plan.shots) * 360 + 120
    assemble_inputs = [
        _link_input("plan", "SC_PLAN"),
        _link_input("policy", "SC_POLICY"),
        _widget_input("video_count", "INT"),
        *[
            _link_input(f"video_{index}", "SC_VIDEO", optional=True)
            for index in range(1, len(shot_video_nodes) + 1)
        ],
    ]
    assemble = graph.add(
        NODE_ASSEMBLE,
        "Assemble Per-Shot Videos",
        (assemble_x, 180),
        inputs=assemble_inputs,
        outputs=[("story_video", "SC_VIDEO")],
        widgets=[len(shot_video_nodes)],
        size=(340, 180 + 24 * len(shot_video_nodes)),
    )
    graph.connect(director_node, 0, assemble, 0, "SC_PLAN")
    graph.connect(policy_node, 0, assemble, 1, "SC_POLICY")
    for index, node in enumerate(shot_video_nodes, start=3):
        graph.connect(node, 1, assemble, index, "SC_VIDEO")

    manifest = graph.add(
        NODE_MANIFEST,
        "Auditable Run Manifest",
        (assemble_x + 470, 220),
        inputs=[
            _link_input("plan", "SC_PLAN"),
            _link_input("policy", "SC_POLICY"),
            _link_input("story_video", "SC_VIDEO", optional=True),
        ],
        outputs=[("manifest", "SC_MANIFEST")],
    )
    graph.connect(director_node, 0, manifest, 0, "SC_PLAN")
    graph.connect(policy_node, 0, manifest, 1, "SC_POLICY")
    graph.connect(assemble, 0, manifest, 2, "SC_VIDEO")

    ui_nodes, ui_links = graph.serialize()
    workflow = {
        "revision": 0,
        "last_node_id": graph.next_node_id - 1,
        "last_link_id": graph.next_link_id - 1,
        "nodes": ui_nodes,
        "links": ui_links,
        "groups": [
            {
                "title": "Shared Visual Bible & Reusable Assets",
                "bounding": [900, 0, 430, max(360, shared_y)],
                "color": "#275D6B",
                "font_size": 22,
                "flags": {},
            },
            {
                "title": "Expandable Shot Subgraphs",
                "bounding": [1360, 100, max(500, len(plan.shots) * 360 + 120), 620],
                "color": "#6A4C93",
                "font_size": 22,
                "flags": {},
            },
        ],
        "definitions": {"subgraphs": subgraphs},
        "config": {},
        "extra": {
            "workflowRendererVersion": "LG",
            "storycanvas": {
                "schema_version": plan.schema_version,
                "plan_id": plan.plan_id,
                "story_id": plan.story_id,
                "shot_count": len(plan.shots),
            },
        },
        "version": 0.4,
    }
    api_workflow, node_index = _compile_api(plan, policy_json)
    digest = sha256_json({"workflow": workflow, "api_workflow": api_workflow})
    return CompiledWorkflow(
        plan_id=plan.plan_id,
        workflow=workflow,
        api_workflow=api_workflow,
        node_index=node_index,
        workflow_sha256=digest,
        warnings=list(plan.warnings),
    )


def pretty_workflow(compiled: CompiledWorkflow) -> str:
    return json.dumps(compiled.workflow, ensure_ascii=False, indent=2) + "\n"
