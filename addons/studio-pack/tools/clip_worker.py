"""Runs inside ~/video-pipeline/.voice-venv (PyTorch + transformers): CLIP image and text embeddings for footage
search. JSON on stdin: {"images": [paths], "out": file.npy} or {"texts": [...], "out": file.npy}.
Embeddings are L2-normalised, so a dot product is the cosine similarity."""
import json
import sys

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

MODEL = "openai/clip-vit-base-patch32"


def features(out):
    return out if torch.is_tensor(out) else out.pooler_output  # transformers 5 wraps the projection


def main() -> None:
    job = json.load(sys.stdin)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(MODEL).to(device).eval()
    proc = CLIPProcessor.from_pretrained(MODEL)
    chunks = []
    with torch.no_grad():
        if "texts" in job:
            inputs = proc(text=job["texts"], return_tensors="pt", padding=True).to(device)
            chunks.append(features(model.get_text_features(**inputs)))
        else:
            paths = job["images"]
            for i in range(0, len(paths), 64):
                imgs = [Image.open(p).convert("RGB") for p in paths[i:i + 64]]
                inputs = proc(images=imgs, return_tensors="pt").to(device)
                chunks.append(features(model.get_image_features(**inputs)))
    emb = torch.cat(chunks).float()
    emb = emb / emb.norm(dim=-1, keepdim=True)
    np.save(job["out"], emb.cpu().numpy().astype(np.float16 if "images" in job else np.float32))
    print(json.dumps({"count": int(emb.shape[0]), "device": device}))


if __name__ == "__main__":
    main()
