"""ComfyUI API-biçimli iş akışı şablonları (standart çekirdek düğümler). Model/denetim noktası adları Ayarlar'dan gelir."""
from __future__ import annotations

from typing import Any, Dict


def _ksampler(seed: int, steps: int, cfg: float, model: Any, pos: Any, neg: Any, latent: Any, denoise: float = 1.0) -> Dict[str, Any]:
    return {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal",
                                                "denoise": denoise, "model": model, "positive": pos, "negative": neg, "latent_image": latent}}


def _encode(prompt: str, negative: str, clip: Any) -> Dict[str, Any]:
    return {"pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": clip}},
            "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": clip}}}


def txt2img(prompt: str, negative: str, width: int, height: int, seed: int, steps: int, cfg: float, checkpoint: str) -> Dict[str, Any]:
    e = _encode(prompt, negative, ["1", 1])
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "2": e["pos"], "3": e["neg"],
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "5": _ksampler(seed, steps, cfg, ["1", 0], ["2", 0], ["3", 0], ["4", 0]),
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "kadraj"}}}


def upscale(image_name: str, model_name: str, scale_by: float) -> Dict[str, Any]:
    """Model ×4 ise ``scale_by`` ile küçültülerek ×2 elde edilir (``ImageScaleBy``)."""
    wf: Dict[str, Any] = {"1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
                          "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model_name}},
                          "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}}}
    last = "3"
    if abs(scale_by - 1.0) > 1e-6:
        wf["4"] = {"class_type": "ImageScaleBy", "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "scale_by": scale_by}}
        last = "4"
    wf["9"] = {"class_type": "SaveImage", "inputs": {"images": [last, 0], "filename_prefix": "kadraj_up"}}
    return wf


def inpaint(image_name: str, mask_name: str, prompt: str, negative: str, seed: int, steps: int, cfg: float, checkpoint: str,
            grow: int = 12) -> Dict[str, Any]:
    e = _encode(prompt, negative, ["1", 1])
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "2": e["pos"], "3": e["neg"],
            "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "5": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
            "6": {"class_type": "VAEEncodeForInpaint", "inputs": {"pixels": ["4", 0], "vae": ["1", 2], "mask": ["5", 0], "grow_mask_by": grow}},
            "7": _ksampler(seed, steps, cfg, ["1", 0], ["2", 0], ["3", 0], ["6", 0]),
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "kadraj_inpaint"}}}


def img2img(image_name: str, prompt: str, negative: str, seed: int, steps: int, cfg: float, checkpoint: str, denoise: float) -> Dict[str, Any]:
    e = _encode(prompt, negative, ["1", 1])
    return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "2": e["pos"], "3": e["neg"],
            "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["1", 2]}},
            "6": _ksampler(seed, steps, cfg, ["1", 0], ["2", 0], ["3", 0], ["5", 0], denoise),
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "kadraj_style"}}}
