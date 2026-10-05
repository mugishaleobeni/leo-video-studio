"""Real Wan inference. No sample video or synthetic placeholder backend."""
import os
import gc
import hashlib
import json
from pathlib import Path
from planner import STYLES

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

NEGATIVE_PROMPT = "blurry, distorted anatomy, extra limbs, malformed hands, illegible text, watermark, frozen motion"

PROFILES = {
    'Quality 14B / 720p': ('Wan-AI/Wan2.1-T2V-14B-Diffusers', 1280, 720),
    'Economy 1.3B / 480p': ('Wan-AI/Wan2.1-T2V-1.3B-Diffusers', 832, 480),
}

class WanRenderer:
    def __init__(self):
        self.pipe = None
        self.profile = None
        self.loaded_low_memory = None

    @staticmethod
    def prompt(shot, plan):
        return shot['prompt'] + '\n' + STYLES[plan['style']] + '\nCharacter appearance: ' + plan['characters']

    @staticmethod
    def low_memory(profile):
        return profile == 'Economy 1.3B / 480p' and os.getenv('LOW_MEMORY', '1') == '1'

    def embedding_path(self, prompt, profile, directory):
        identity = [PROFILES[profile][0], prompt, NEGATIVE_PROMPT, 'nf4-fp16-mask512-v1']
        digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        return Path(directory) / '.prompt_embeddings' / (digest + '.pt')

    def release(self):
        import torch
        self.pipe = None
        self.profile = None
        self.loaded_low_memory = None
        gc.collect()
        torch.cuda.empty_cache()

    def prepare(self, plan, profile, directory, cancelled=lambda: False):
        """Encode one prompt at a time, then unload UMT5 before loading video weights."""
        if not self.low_memory(profile):
            return
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('Real video generation requires an online NVIDIA GPU with CUDA.')
        pending = {}
        for episode in plan['episodes']:
            for shot in episode['shots']:
                prompt = self.prompt(shot, plan)
                path = self.embedding_path(prompt, profile, directory)
                if not path.is_file():
                    pending[path] = prompt
        if not pending or cancelled():
            return
        self.release()
        from transformers import AutoTokenizer, UMT5EncoderModel, BitsAndBytesConfig
        model_id = PROFILES[profile][0]
        tokenizer = AutoTokenizer.from_pretrained(model_id, subfolder='tokenizer')
        # Validate before downloading/loading the large text encoder.
        for prompt in pending.values():
            if len(tokenizer(prompt, truncation=False)['input_ids']) > 512:
                raise ValueError('Shot plus character description exceeds the model text limit; shorten the shot or character bible')
        quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.float16)
        encoder = None
        try:
            encoder = UMT5EncoderModel.from_pretrained(model_id, subfolder='text_encoder',
                quantization_config=quantization, device_map={'': 0}, dtype=torch.float16,
                low_cpu_mem_usage=True)
            @torch.inference_mode()
            def encode(text):
                tokens = tokenizer(text, padding='max_length', max_length=512,
                    truncation=True, return_tensors='pt').to('cuda')
                embeddings = encoder(**tokens).last_hidden_state
                length = int(tokens.attention_mask.sum().item())
                embeddings[:, length:] = 0
                return embeddings.cpu()
            negative = encode(NEGATIVE_PROMPT)
            for path, prompt in pending.items():
                if cancelled():
                    break
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.partial.pt')
                torch.save({'prompt_embeds': encode(prompt), 'negative_prompt_embeds': negative}, temporary)
                os.replace(temporary, path)
        finally:
            encoder = None
            gc.collect()
            torch.cuda.empty_cache()

    def load(self, profile):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('Real video generation requires an online NVIDIA GPU with CUDA. No placeholder output will be generated.')
        if profile not in PROFILES:
            raise ValueError('Unsupported model profile')
        if self.profile == profile and self.pipe is not None and self.loaded_low_memory == self.low_memory(profile):
            return
        self.release()
        from diffusers import AutoencoderKLWan, WanPipeline
        model_id, _, _ = PROFILES[profile]
        dtype = torch.float16 if self.low_memory(profile) else (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
        vae = AutoencoderKLWan.from_pretrained(model_id, subfolder='vae', torch_dtype=torch.float32)
        options = {'text_encoder': None} if self.low_memory(profile) else {}
        pipe = WanPipeline.from_pretrained(model_id, vae=vae, torch_dtype=dtype,
            low_cpu_mem_usage=True, **options)
        pipe.vae.enable_tiling()
        if os.getenv('CPU_OFFLOAD', '1') == '1':
            pipe.enable_model_cpu_offload()
        else:
            pipe.to('cuda')
        self.pipe, self.profile = pipe, profile
        self.loaded_low_memory = self.low_memory(profile)

    def render(self, shot, plan, path, profile, seed, steps):
        import torch
        from diffusers.utils import export_to_video
        from contextlib import nullcontext
        from torch.nn.attention import sdpa_kernel, SDPBackend
        prompt = self.prompt(shot, plan)
        if self.low_memory(profile):
            cache = self.embedding_path(prompt, profile, Path(path).parent.parent)
            if not cache.is_file():
                self.prepare({'episodes': [{'shots': [shot]}], 'style': plan['style'],
                    'characters': plan['characters']}, profile, Path(path).parent.parent)
            self.load(profile)
            saved = torch.load(cache, map_location='cpu', weights_only=True)
            conditioning = {name: tensor.to(device='cuda', dtype=self.pipe.transformer.dtype)
                for name, tensor in saved.items()}
        else:
            self.load(profile)
            tokens = self.pipe.tokenizer(prompt, truncation=False)['input_ids']
            if len(tokens) > 512:
                raise ValueError('Shot plus character description exceeds the model text limit; shorten the shot or character bible')
            conditioning = {'prompt': prompt, 'negative_prompt': NEGATIVE_PROMPT}
        _, width, height = PROFILES[profile]
        try:
            attention = sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION) if self.low_memory(profile) else nullcontext()
            with torch.inference_mode(), attention:
                frames = self.pipe(**conditioning, height=height, width=width,
                    num_frames=81, num_inference_steps=int(steps), guidance_scale=5.0,
                    generator=torch.Generator(device='cpu').manual_seed(int(seed)),
                    max_sequence_length=512).frames[0]
        finally:
            conditioning.clear()
            self.pipe.maybe_free_model_hooks()
        temporary = Path(path).with_name(Path(path).stem + '.partial.mp4')
        export_to_video(frames, str(temporary), fps=16)
        os.replace(temporary, path)
