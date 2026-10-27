import json, threading
from store import HOME

REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
FINGERPRINT = "embeddinggemma2:" + REVISION + ":bf16-mps-mean768:v1"
LOCK = threading.RLock()
SCHEDULE = threading.Condition()
BUSY = False
QUERIES = 0
MODEL = None
PROCESSOR = None


def encode(priority=False, **kwargs):
    global BUSY, QUERIES
    with SCHEDULE:
        if priority:
            QUERIES += 1
        try:
            SCHEDULE.wait_for(lambda: not BUSY and (priority or QUERIES == 0))
            BUSY = True
        finally:
            if priority:
                QUERIES -= 1
    try:
        return _encode(**kwargs)
    finally:
        with SCHEDULE:
            BUSY = False
            SCHEDULE.notify_all()


def _encode(**kwargs):
    global MODEL, PROCESSOR
    with LOCK:
        import torch
        import numpy as np
        from transformers import AutoModel, AutoProcessor

        if MODEL is None:
            if not torch.backends.mps.is_available():
                raise RuntimeError(
                    "Apple GPU unavailable. Run the local service on your Apple silicon Mac."
                )
            runtime = json.loads((HOME / "runtime.json").read_text())
            path = runtime["model"]
            torch.set_num_threads(4)
            PROCESSOR = AutoProcessor.from_pretrained(path, local_files_only=True)
            MODEL = (
                AutoModel.from_pretrained(
                    path,
                    local_files_only=True,
                    dtype=torch.bfloat16,
                    attn_implementation={
                        "": "eager",
                        "text_config": "sdpa",
                        "vision_config": "sdpa",
                        "audio_config": "eager",
                    },
                )
                .to("mps")
                .eval()
            )
        inputs = PROCESSOR(
            return_tensors="pt", padding=True, truncation=False, **kwargs
        )
        if inputs["attention_mask"].sum(dim=1).max().item() > 8192:
            raise ValueError(
                "Input exceeds embedding context; split into smaller segments."
            )
        inputs = {
            k: v.to(device="mps", dtype=torch.bfloat16)
            if torch.is_floating_point(v)
            else v.to("mps")
            for k, v in inputs.items()
            if isinstance(v, torch.Tensor)
        }
        try:
            with torch.inference_mode():
                out = MODEL(**inputs)
                mask = (
                    inputs["attention_mask"]
                    .unsqueeze(-1)
                    .to(out.last_hidden_state.dtype)
                )
                vec = (
                    torch.nn.functional.normalize(
                        ((out.last_hidden_state * mask).sum(1) / mask.sum(1)).float(),
                        dim=-1,
                    )
                    .cpu()
                    .numpy()
                )
            if vec.shape[-1] != 768 or not np.isfinite(vec).all():
                raise RuntimeError("Embedding output is invalid.")
            return vec
        finally:
            del inputs
            torch.mps.empty_cache()
