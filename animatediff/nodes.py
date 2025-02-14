import json
import os
import shutil
import subprocess
from typing import Dict, List

import numpy as np
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import comfy.sample as comfy_sample
import folder_paths
import nodes as comfy_nodes
from comfy.model_patcher import ModelPatcher
from comfy.sd import load_checkpoint_guess_config
from .context import ContextOptions, ContextSchedules, UniformContextOptions
from .logger import logger
from .model_utils import IsChangedHelper, get_available_motion_loras, get_available_motion_models, BetaSchedules, \
    raise_if_not_checkpoint_sd1_5
from .motion_lora import MotionLoRAInfo, MotionLoRAList
from .motion_module import InjectorVersion, InjectionParams, MotionModelSettings
from .motion_module import eject_params_from_model, inject_params_into_model, load_motion_lora, load_motion_module
from .sampling import animatediff_sample_factory

# override comfy_sample.sample with animatediff-support version
comfy_sample.sample = animatediff_sample_factory(comfy_sample.sample)


class AnimateDiffLoRALoader:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "lora_name": (get_available_motion_loras(),),
                "strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.001}),
            },
            "optional": {
                "prev_motion_lora": ("MOTION_LORA",),
            }
        }
    
    RETURN_TYPES = ("MOTION_LORA",)
    CATEGORY = "Animate Diff 🎭🅐🅓"
    FUNCTION = "load_motion_lora"

    def load_motion_lora(self, lora_name: str, strength: float, prev_motion_lora: MotionLoRAList=None):
        if prev_motion_lora is None:
            prev_motion_lora = MotionLoRAList()
        else:
            prev_motion_lora = prev_motion_lora.clone()
        # load lora
        lora = load_motion_lora(lora_name)
        lora_info = MotionLoRAInfo(name=lora_name, strength=strength, hash=lora.hash)
        prev_motion_lora.add_lora(lora_info)

        return (prev_motion_lora,)


class AnimateDiffModelSettingsSimple:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "motion_pe_stretch": ("INT", {"default": 0, "min": 0, "step": 1}),
            },
        }
    
    RETURN_TYPES = ("MOTION_MODEL_SETTINGS",)
    CATEGORY = "Animate Diff 🎭🅐🅓/motion settings"
    FUNCTION = "get_motion_model_settings"

    def get_motion_model_settings(self, motion_pe_stretch: int):
        motion_model_settings = MotionModelSettings(
            motion_pe_stretch=motion_pe_stretch
            )

        return (motion_model_settings,)


class AnimateDiffModelSettingsAdvanced:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pe_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "attn_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "other_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "motion_pe_stretch": ("INT", {"default": 0, "min": 0, "step": 1}),
                "cap_initial_pe_length": ("INT", {"default": 0, "min": 0, "step": 1}),
                "interpolate_pe_to_length": ("INT", {"default": 0, "min": 0, "step": 1}),
                "initial_pe_idx_offset": ("INT", {"default": 0, "min": 0, "step": 1}),
                "final_pe_idx_offset": ("INT", {"default": 0, "min": 0, "step": 1}),
            },
        }
    
    RETURN_TYPES = ("MOTION_MODEL_SETTINGS",)
    CATEGORY = "Animate Diff 🎭🅐🅓/motion settings"
    FUNCTION = "get_motion_model_settings"

    def get_motion_model_settings(self, pe_strength: float, attn_strength: float, other_strength: float,
                                  motion_pe_stretch: int,
                                  cap_initial_pe_length: int, interpolate_pe_to_length: int,
                                  initial_pe_idx_offset: int, final_pe_idx_offset: int):
        motion_model_settings = MotionModelSettings(
            pe_strength=pe_strength,
            attn_strength=attn_strength,
            other_strength=other_strength,
            cap_initial_pe_length=cap_initial_pe_length,
            interpolate_pe_to_length=interpolate_pe_to_length,
            initial_pe_idx_offset=initial_pe_idx_offset,
            final_pe_idx_offset=final_pe_idx_offset,
            motion_pe_stretch=motion_pe_stretch
            )

        return (motion_model_settings,)


class AnimateDiffModelSettingsAdvancedAttnStrengths:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pe_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "attn_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "attn_q_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "attn_k_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "attn_v_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "attn_out_weight_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "attn_out_bias_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "other_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.0001}),
                "motion_pe_stretch": ("INT", {"default": 0, "min": 0, "step": 1}),
                "cap_initial_pe_length": ("INT", {"default": 0, "min": 0, "step": 1}),
                "interpolate_pe_to_length": ("INT", {"default": 0, "min": 0, "step": 1}),
                "initial_pe_idx_offset": ("INT", {"default": 0, "min": 0, "step": 1}),
                "final_pe_idx_offset": ("INT", {"default": 0, "min": 0, "step": 1}),
            },
        }
    
    RETURN_TYPES = ("MOTION_MODEL_SETTINGS",)
    CATEGORY = "Animate Diff 🎭🅐🅓/motion settings"
    FUNCTION = "get_motion_model_settings"

    def get_motion_model_settings(self, pe_strength: float, attn_strength: float,
                                  attn_q_strength: float,
                                  attn_k_strength: float,
                                  attn_v_strength: float,
                                  attn_out_weight_strength: float,
                                  attn_out_bias_strength: float,
                                  other_strength: float,
                                  motion_pe_stretch: int,
                                  cap_initial_pe_length: int, interpolate_pe_to_length: int,
                                  initial_pe_idx_offset: int, final_pe_idx_offset: int):
        motion_model_settings = MotionModelSettings(
            pe_strength=pe_strength,
            attn_strength=attn_strength,
            attn_q_strength=attn_q_strength,
            attn_k_strength=attn_k_strength,
            attn_v_strength=attn_v_strength,
            attn_out_weight_strength=attn_out_weight_strength,
            attn_out_bias_strength=attn_out_bias_strength,
            other_strength=other_strength,
            cap_initial_pe_length=cap_initial_pe_length,
            interpolate_pe_to_length=interpolate_pe_to_length,
            initial_pe_idx_offset=initial_pe_idx_offset,
            final_pe_idx_offset=final_pe_idx_offset,
            motion_pe_stretch=motion_pe_stretch
            )

        return (motion_model_settings,)


class AnimateDiffLoaderWithContext:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "model_name": (get_available_motion_models(),),
                "beta_schedule": (BetaSchedules.get_alias_list_with_first_element(BetaSchedules.SQRT_LINEAR),),
                #"apply_mm_groupnorm_hack": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "context_options": ("CONTEXT_OPTIONS",),
                "motion_lora": ("MOTION_LORA",),
                "motion_model_settings": ("MOTION_MODEL_SETTINGS",),
                "motion_scale": ("FLOAT", {"default": 1.0, "min": 0.0, "step": 0.001}),
                "apply_v2_models_properly": ("BOOLEAN", {"default": False}),
            }
        }
    
    RETURN_TYPES = ("MODEL",)
    CATEGORY = "Animate Diff 🎭🅐🅓"
    FUNCTION = "load_mm_and_inject_params"


    def load_mm_and_inject_params(self,
        model: ModelPatcher,
        model_name: str, beta_schedule: str,# apply_mm_groupnorm_hack: bool,
        context_options: ContextOptions=None, motion_lora: MotionLoRAList=None, motion_model_settings: MotionModelSettings=None,
        motion_scale: float=1.0, apply_v2_models_properly: bool=False,
    ):
        # load motion module
        mm = load_motion_module(model_name, motion_lora, model=model, motion_model_settings=motion_model_settings)
        # set injection params
        injection_params = InjectionParams(
                video_length=None,
                unlimited_area_hack=False,
                apply_mm_groupnorm_hack=True,
                beta_schedule=beta_schedule,
                injector=mm.injector_version,
                model_name=model_name,
                apply_v2_models_properly=apply_v2_models_properly,
        )
        if context_options:
            # set context settings TODO: make this dynamic for future purposes
            if type(context_options) == UniformContextOptions:
                injection_params.set_context(
                        context_length=context_options.context_length,
                        context_stride=context_options.context_stride,
                        context_overlap=context_options.context_overlap,
                        context_schedule=context_options.context_schedule,
                        closed_loop=context_options.closed_loop,
                        sync_context_to_pe=context_options.sync_context_to_pe
                )
        if motion_lora:
            injection_params.set_loras(motion_lora)
        # set motion_scale and motion_model_settings
        if not motion_model_settings:
            motion_model_settings = MotionModelSettings()
        motion_model_settings.attn_scale = motion_scale
        injection_params.set_motion_model_settings(motion_model_settings)
        # inject for use in sampling code
        model = inject_params_into_model(model, injection_params)

        return (model,)


class AnimateDiffUniformContextOptions:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "context_length": ("INT", {"default": 16, "min": 1, "max": 32}), # keep an eye on these max values
                "context_stride": ("INT", {"default": 1, "min": 1, "max": 32}),  # would need to be updated
                "context_overlap": ("INT", {"default": 4, "min": 0, "max": 32}), # if new motion modules come out
                "context_schedule": (ContextSchedules.CONTEXT_SCHEDULE_LIST,),
                "closed_loop": ("BOOLEAN", {"default": False},),
                #"sync_context_to_pe": ("BOOLEAN", {"default": False},),
            },
        }
    
    RETURN_TYPES = ("CONTEXT_OPTIONS",)
    CATEGORY = "Animate Diff 🎭🅐🅓"
    FUNCTION = "create_options"

    def create_options(self, context_length: int, context_stride: int, context_overlap: int, context_schedule: int, closed_loop: bool):
        context_options = UniformContextOptions(
            context_length=context_length,
            context_stride=context_stride,
            context_overlap=context_overlap,
            context_schedule=context_schedule,
            closed_loop=closed_loop
            )
        #context_options.set_sync_context_to_pe(sync_context_to_pe)
        return (context_options,)



class AnimateDiffLoader_Deprecated:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "latents": ("LATENT",),
                "model_name": (get_available_motion_models(),),
                "unlimited_area_hack": ("BOOLEAN", {"default": False},),
                "beta_schedule": (BetaSchedules.get_alias_list_with_first_element(BetaSchedules.SQRT_LINEAR),),
            },
        }

    RETURN_TYPES = ("MODEL", "LATENT")
    CATEGORY = "Animate Diff 🎭🅐🅓/deprecated (DO NOT USE)"
    FUNCTION = "load_mm_and_inject_params"

    def load_mm_and_inject_params(
        self,
        model: ModelPatcher,
        latents: Dict[str, torch.Tensor],
        model_name: str, unlimited_area_hack: bool, beta_schedule: str,
    ):
        raise_if_not_checkpoint_sd1_5(model)
        # load motion module
        load_motion_module(model_name)
        # get total frames
        init_frames_len = len(latents["samples"])
        # set injection params
        injection_params = InjectionParams(
                video_length=init_frames_len,
                unlimited_area_hack=unlimited_area_hack,
                apply_mm_groupnorm_hack=True,
                beta_schedule=beta_schedule,
                injector=InjectorVersion.V1_V2,
                model_name=model_name,
        )
        # inject for use in sampling code
        model = inject_params_into_model(model, injection_params)

        return (model, latents)


class AnimateDiffLoaderAdvanced_Deprecated:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "latents": ("LATENT",),
                "model_name": (get_available_motion_models(),),
                "unlimited_area_hack": ("BOOLEAN", {"default": False},),
                "context_length": ("INT", {"default": 16, "min": 0, "max": 1000}),
                "context_stride": ("INT", {"default": 1, "min": 1, "max": 1000}),
                "context_overlap": ("INT", {"default": 4, "min": 0, "max": 1000}),
                "context_schedule": (ContextSchedules.CONTEXT_SCHEDULE_LIST,),
                "closed_loop": ("BOOLEAN", {"default": False},),
                "beta_schedule": (BetaSchedules.get_alias_list_with_first_element(BetaSchedules.SQRT_LINEAR),),
            },
        }

    RETURN_TYPES = ("MODEL", "LATENT")
    CATEGORY = "Animate Diff 🎭🅐🅓/deprecated (DO NOT USE)"
    FUNCTION = "load_mm_and_inject_params"

    def load_mm_and_inject_params(self,
            model: ModelPatcher,
            latents: Dict[str, torch.Tensor],
            model_name: str, unlimited_area_hack: bool,
            context_length: int, context_stride: int, context_overlap: int, context_schedule: str, closed_loop: bool,
            beta_schedule: str,
        ):
        raise_if_not_checkpoint_sd1_5(model)
        # load motion module
        load_motion_module(model_name)
        # get total frames
        init_frames_len = len(latents["samples"])
        # set injection params
        injection_params = InjectionParams(
                video_length=init_frames_len,
                unlimited_area_hack=unlimited_area_hack,
                apply_mm_groupnorm_hack=True,
                beta_schedule=beta_schedule,
                injector=InjectorVersion.V1_V2,
                model_name=model_name,
        )
        # set context settings
        injection_params.set_context(
                context_length=context_length,
                context_stride=context_stride,
                context_overlap=context_overlap,
                context_schedule=context_schedule,
                closed_loop=closed_loop
        )
        # inject for use in sampling code
        model = inject_params_into_model(model, injection_params)

        return (model, latents)


class AnimateDiffUnload:
    def __init__(self) -> None:
        self.change = IsChangedHelper()

    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"model": ("MODEL",)}}

    RETURN_TYPES = ("MODEL",)
    CATEGORY = "Animate Diff 🎭🅐🅓"
    FUNCTION = "unload_motion_modules"

    def unload_motion_modules(self, model: ModelPatcher):
        # return model clone with ejected params
        model = eject_params_from_model(model)

        return (model,)


class AnimateDiffCombine_Deprecated:
    @classmethod
    def INPUT_TYPES(s):
        ffmpeg_path = shutil.which("ffmpeg")
        #Hide ffmpeg formats if ffmpeg isn't available
        if ffmpeg_path is not None:
            ffmpeg_formats = ["video/"+x[:-5] for x in folder_paths.get_filename_list("video_formats")]
        else:
            ffmpeg_formats = []
            logger.warning("ffmpeg could not be found. Outputs that require it have been disabled")
        return {
            "required": {
                "images": ("IMAGE",),
                "frame_rate": (
                    "INT",
                    {"default": 8, "min": 1, "max": 24, "step": 1},
                ),
                "loop_count": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1}),
                "filename_prefix": ("STRING", {"default": "AnimateDiff"}),
                "format": (["image/gif", "image/webp"] + ffmpeg_formats,),
                "pingpong": ("BOOLEAN", {"default": False}),
                "save_image": ("BOOLEAN", {"default": True}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("GIF",)
    OUTPUT_NODE = True
    CATEGORY = "Animate Diff 🎭🅐🅓/deprecated (DO NOT USE)"
    FUNCTION = "generate_gif"

    def generate_gif(
        self,
        images,
        frame_rate: int,
        loop_count: int,
        filename_prefix="AnimateDiff",
        format="image/gif",
        pingpong=False,
        save_image=True,
        prompt=None,
        extra_pnginfo=None,
    ):
        # convert images to numpy
        frames: List[Image.Image] = []
        for image in images:
            img = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
            frames.append(img)
            
        # get output information
        output_dir = (
            folder_paths.get_output_directory()
            if save_image
            else folder_paths.get_temp_directory()
        )
        (
            full_output_folder,
            filename,
            counter,
            subfolder,
            _,
        ) = folder_paths.get_save_image_path(filename_prefix, output_dir)

        metadata = PngInfo()
        if prompt is not None:
            metadata.add_text("prompt", json.dumps(prompt))
        if extra_pnginfo is not None:
            for x in extra_pnginfo:
                metadata.add_text(x, json.dumps(extra_pnginfo[x]))

        # save first frame as png to keep metadata
        file = f"{filename}_{counter:05}_.png"
        file_path = os.path.join(full_output_folder, file)
        frames[0].save(
            file_path,
            pnginfo=metadata,
            compress_level=4,
        )
        if pingpong:
            frames = frames + frames[-2:0:-1]
        
        format_type, format_ext = format.split("/")
        file = f"{filename}_{counter:05}_.{format_ext}"
        file_path = os.path.join(full_output_folder, file)
        if format_type == "image":
            # Use pillow directly to save an animated image
            frames[0].save(
                file_path,
                format=format_ext.upper(),
                save_all=True,
                append_images=frames[1:],
                duration=round(1000 / frame_rate),
                loop=loop_count,
                compress_level=4,
            )
        else:
            # Use ffmpeg to save a video
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path is None:
                #Should never be reachable
                raise ProcessLookupError("Could not find ffmpeg")

            video_format_path = folder_paths.get_full_path("video_formats", format_ext + ".json")
            with open(video_format_path, 'r') as stream:
                video_format = json.load(stream)
            file = f"{filename}_{counter:05}_.{video_format['extension']}"
            file_path = os.path.join(full_output_folder, file)
            dimensions = f"{frames[0].width}x{frames[0].height}"
            args = [ffmpeg_path, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", dimensions, "-r", str(frame_rate), "-i", "-"] \
                    + video_format['main_pass'] + [file_path]

            env=os.environ.copy()
            if  "environment" in video_format:
                env.update(video_format["environment"])
            with subprocess.Popen(args, stdin=subprocess.PIPE, env=env) as proc:
                for frame in frames:
                    proc.stdin.write(frame.tobytes())

        previews = [
            {
                "filename": file,
                "subfolder": subfolder,
                "type": "output" if save_image else "temp",
                "format": format,
            }
        ]
        return {"ui": {"gifs": previews}}

class CheckpointLoaderSimpleWithNoiseSelect:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "ckpt_name": (folder_paths.get_filename_list("checkpoints"), ),
                "beta_schedule": (BetaSchedules.get_alias_list_with_first_element(BetaSchedules.LINEAR), )
            },
        }
    RETURN_TYPES = ("MODEL", "CLIP", "VAE")
    FUNCTION = "load_checkpoint"

    CATEGORY = "Animate Diff 🎭🅐🅓/extras"

    def load_checkpoint(self, ckpt_name, beta_schedule, output_vae=True, output_clip=True):
        ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
        out = load_checkpoint_guess_config(ckpt_path, output_vae=True, output_clip=True, embedding_directory=folder_paths.get_folder_paths("embeddings"))
        # register chosen beta schedule on model - convert to beta_schedule name recognized by ComfyUI
        out[0].model.model_sampling = BetaSchedules.to_model_sampling(beta_schedule, out[0])
        return out


class EmptyLatentImageLarge:
    def __init__(self, device="cpu"):
        self.device = device

    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "width": ("INT", {"default": 512, "min": 64, "max": comfy_nodes.MAX_RESOLUTION, "step": 8}),
                              "height": ("INT", {"default": 512, "min": 64, "max": comfy_nodes.MAX_RESOLUTION, "step": 8}),
                              "batch_size": ("INT", {"default": 1, "min": 1, "max": 262144})}}
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "generate"

    CATEGORY = "Animate Diff 🎭🅐🅓/extras"

    def generate(self, width, height, batch_size=1):
        latent = torch.zeros([batch_size, 4, height // 8, width // 8])
        return ({"samples":latent}, )


NODE_CLASS_MAPPINGS = {
    "ADE_AnimateDiffUniformContextOptions": AnimateDiffUniformContextOptions,
    "ADE_AnimateDiffLoaderWithContext": AnimateDiffLoaderWithContext,
    "ADE_AnimateDiffLoRALoader": AnimateDiffLoRALoader,
    "ADE_AnimateDiffModelSettingsSimple": AnimateDiffModelSettingsSimple,
    "ADE_AnimateDiffModelSettings": AnimateDiffModelSettingsAdvanced,
    "ADE_AnimateDiffModelSettingsAdvancedAttnStrengths": AnimateDiffModelSettingsAdvancedAttnStrengths,
    "ADE_AnimateDiffUnload": AnimateDiffUnload,
    "ADE_EmptyLatentImageLarge": EmptyLatentImageLarge,
    "CheckpointLoaderSimpleWithNoiseSelect": CheckpointLoaderSimpleWithNoiseSelect,
    "AnimateDiffLoaderV1": AnimateDiffLoader_Deprecated,
    "ADE_AnimateDiffLoaderV1Advanced": AnimateDiffLoaderAdvanced_Deprecated,
    "ADE_AnimateDiffCombine": AnimateDiffCombine_Deprecated,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ADE_AnimateDiffUniformContextOptions": "Uniform Context Options 🎭🅐🅓",
    "ADE_AnimateDiffLoaderWithContext": "AnimateDiff Loader 🎭🅐🅓",
    "ADE_AnimateDiffLoRALoader": "AnimateDiff LoRA Loader 🎭🅐🅓",
    "ADE_AnimateDiffModelSettingsSimple": "Motion Model Settings (Simple) 🎭🅐🅓",
    "ADE_AnimateDiffModelSettings": "Motion Model Settings (Advanced) 🎭🅐🅓",
    "ADE_AnimateDiffModelSettingsAdvancedAttnStrengths": "Motion Model Settings (Adv. Attn) 🎭🅐🅓",
    "ADE_AnimateDiffUnload": "AnimateDiff Unload 🎭🅐🅓",
    "ADE_EmptyLatentImageLarge": "Empty Latent Image (Big Batch) 🎭🅐🅓",
    "CheckpointLoaderSimpleWithNoiseSelect": "Load Checkpoint w/ Noise Select 🎭🅐🅓",
    "AnimateDiffLoaderV1": "AnimateDiff Loader [DEPRECATED] 🎭🅐🅓",
    "ADE_AnimateDiffLoaderV1Advanced": "AnimateDiff Loader (Advanced) [DEPRECATED] 🎭🅐🅓",
    "ADE_AnimateDiffCombine": "AnimateDiff Combine [DEPRECATED] 🎭🅐🅓",
}
