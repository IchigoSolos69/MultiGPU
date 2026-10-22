"""Run from MultiGPU's Custom recipe; no dependencies needed for this example."""
import json
import random


def train(ctx):
    rng = random.Random(ctx.spec["seed"])
    weight = 0.0
    if ctx.resume_state:
        weight = ctx.resume_state["weight"]
        # JSON round-trips tuples as lists.
        def tuples(value):
            return tuple(tuples(v) for v in value) if isinstance(value, list) else value
        rng.setstate(tuples(ctx.resume_state["rng_state"]))
    for step in range(ctx.step + 1, ctx.spec["steps"] + 1):
        if ctx.should_stop():
            break
        x = rng.uniform(-1, 1)
        target = 2 * x
        error = weight * x - target
        weight -= ctx.spec["learning_rate"] * 2 * error * x
        ctx.report(error * error, step)
        ctx.checkpoint({"weight": weight, "rng_state": rng.getstate()})
    # Save the current step even if the session ended before its first update.
    ctx.checkpoint({"weight": weight, "rng_state": rng.getstate()})
    if ctx.step == ctx.spec["steps"]:
        ctx.export({"model.json": json.dumps({"weight": weight, "prediction": "weight * x"})})
