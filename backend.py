"""Real Wan inference. No sample video or synthetic placeholder backend."""
import os
from pathlib import Path
from planner import STYLES

PROFILES = {
    'Quality 14B / 720p': ('Wan-AI/Wan2.1-T2V-14B-Diffusers', 1280, 720),
    'Economy 1.3B / 480p': ('Wan-AI/Wan2.1-T2V-1.3B-Diffusers', 832, 480),
}

class WanRenderer:
    def __init__(self):
        self.pipe = None
        self.profile = None

    def load(self, profile):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('Real video generation requires an online NVIDIA GPU with CUDA. No placeholder output will be generated.')
        if profile not in PROFILES:
            raise ValueError('Unsupported model profile')
        if self.profile == profile and self.pipe is not None:
            return
        if self.pipe is not None:
            del self.pipe
            self.pipe = None
            import gc
            gc.collect()
            torch.cuda.empty_cache()
        from diffusers import AutoencoderKLWan, WanPipeline
        model_id, _, _ = PROFILES[profile]
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        vae = AutoencoderKLWan.from_pretrained(model_id, subfolder='vae', torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(model_id, vae=vae, torch_dtype=dtype)
        pipe.vae.enable_tiling()
        if os.getenv('CPU_OFFLOAD', '1') == '1':
            pipe.enable_model_cpu_offload()
        else:
            pipe.to('cuda')
        self.pipe, self.profile = pipe, profile

    def render(self, shot, plan, path, profile, seed, steps):
        import torch
        from diffusers.utils import export_to_video
        self.load(profile)
        _, width, height = PROFILES[profile]
        # Put the visible action first; repeated character bible encourages, but does not guarantee, identity consistency.
        prompt = shot['prompt'] + '\n' + STYLES[plan['style']] + '\nCharacter appearance: ' + plan['characters']
        tokens = self.pipe.tokenizer(prompt, truncation=False)['input_ids']
        if len(tokens) > 512:
            raise ValueError('Shot plus character description exceeds the model text limit; shorten the shot or character bible')
        frames = self.pipe(prompt=prompt,
            negative_prompt='blurry, distorted anatomy, extra limbs, malformed hands, illegible text, watermark, frozen motion',
            height=height, width=width, num_frames=81, num_inference_steps=int(steps), guidance_scale=5.0,
            generator=torch.Generator(device='cpu').manual_seed(int(seed)), max_sequence_length=512).frames[0]
        temporary = Path(path).with_name(Path(path).stem + '.partial.mp4')
        export_to_video(frames, str(temporary), fps=16)
        os.replace(temporary, path)
