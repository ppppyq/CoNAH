# Third-party attribution

Migration note (2026-11-08): selected HarnessVLN author-release sources are
relocated into this project's planner, session, tools and HM3D platform packages.
The inherited license is saved as HARNESS_VLN_LICENSE. Original-to-current file
mapping and hashes are in examples/HARNESS_VLN_FILES.md and
examples/harness_vln_source_manifest.json. Local modifications cover import paths,
the DeepSeek/Ollama adapters, offline BERT construction and ObjectNav grounding
prompts, including model-specific bounding-box coordinate conversion.
The common tool contracts, registry and runtime were subsequently extracted to
comm_harness/tools; obsolete navigation compatibility paths have been removed.
The stop policy is in session/object_navigation.py and sensor/action capabilities
are in platforms/hm3d_m3/tools.py. Inherited algorithmic portions retain the same
attribution and license; the executor and bindings are integration code.
The launcher and M3 single-episode adapter are project integration code. NavDP
and Depth Anything are listed in the inherited attribution below but are not
vendored or enabled by this migration.

The remainder of this file is the upstream release's attribution text.

The following projects supply reused code or external dependencies. Attribution
here describes upstream work, not authorship of this anonymous submission.
Original copyright/license notices in source files remain intact.

| Component | Upstream |
| --- | --- |
| LaViRA / Uni-LaViRA baseline | https://github.com/NJU-R-L-Group-Embodied-Lab/lavira-code |
| CA-Nav | https://github.com/Chenkehan21/CA-Nav-code |
| Habitat-Lab | https://github.com/facebookresearch/habitat-lab |
| Habitat-Sim | https://github.com/facebookresearch/habitat-sim |
| GroundingDINO | https://github.com/IDEA-Research/GroundingDINO |
| Segment Anything | https://github.com/facebookresearch/segment-anything |
| RepViT | https://github.com/THU-MIG/RepViT |
| NavDP | https://github.com/InternRobotics/NavDP |
| Depth Anything V2 | https://github.com/DepthAnything/Depth-Anything-V2 |

The root LICENSE is the inherited CC BY-NC-SA 4.0 license text. Vendored code
retains its own notices, including `navdp/depth_anything/LICENSE` and Meta /
TensorFlow copyright notices. External dependencies and datasets retain their
respective license and access terms.
