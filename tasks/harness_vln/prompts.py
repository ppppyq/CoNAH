"""Task-specific prompts for navigation and object search."""
import tasks.harness_vln.prompts_vln as prompts_vln
import tasks.harness_vln.prompts_objnav as prompts_objnav

PROMPT_REGISTRY = {"VLN": prompts_vln, "OBJNAV": prompts_objnav}


def get_prompts(task_type):
    try:
        return PROMPT_REGISTRY[task_type]
    except KeyError:
        raise ValueError(f"Unsupported task type {task_type!r}; expected VLN or OBJNAV") from None
