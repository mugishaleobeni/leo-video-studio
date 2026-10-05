import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from backend import WanRenderer

PROFILE = 'Economy 1.3B / 480p'

class MemoryRendererTests(unittest.TestCase):
    def modules(self):
        torch = MagicMock()
        torch.cuda.is_available.return_value = True
        torch.cuda.is_bf16_supported.return_value = False
        torch.float16 = 'fp16'
        torch.float32 = 'fp32'
        @contextlib.contextmanager
        def inference():
            yield
        torch.inference_mode.side_effect = inference
        torch.save.side_effect = lambda data, path: Path(path).write_bytes(b'cached-test')
        tokenizer = MagicMock()
        tokens = MagicMock()
        tokens.to.return_value = tokens
        tokens.attention_mask.sum.return_value.item.return_value = 4
        tokenizer.side_effect = lambda text, **kw: tokens if kw.get('return_tensors') else {'input_ids': [1,2,3]}
        transformers = MagicMock()
        transformers.AutoTokenizer.from_pretrained.return_value = tokenizer
        diffusers = MagicMock()
        return torch, transformers, diffusers

    def test_prepare_caches_and_invalidates_changed_character(self):
        torch, transformers, diffusers = self.modules()
        plan = {'style': 'Realistic', 'characters': 'Blue shirt',
            'episodes': [{'shots': [{'prompt': 'A father waves.'}, {'prompt': 'A father waves.'}]}]}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'LOW_MEMORY':'1'}), patch.dict('sys.modules', {'torch':torch, 'transformers':transformers}):
            renderer = WanRenderer()
            renderer.prepare(plan, PROFILE, temp)
            self.assertEqual(transformers.UMT5EncoderModel.from_pretrained.call_count, 1)
            self.assertEqual(torch.save.call_count, 1)  # duplicate prompts share one cache
            self.assertIsNone(renderer.pipe)
            self.assertEqual(len(list(Path(temp).rglob('*.pt'))), 1)
            renderer.prepare(plan, PROFILE, temp)
            self.assertEqual(transformers.UMT5EncoderModel.from_pretrained.call_count, 1)
            plan['characters'] = 'Yellow coat'
            renderer.prepare(plan, PROFILE, temp)
            self.assertEqual(transformers.UMT5EncoderModel.from_pretrained.call_count, 2)
            renderer.prepare(plan, PROFILE, temp, cancelled=lambda: True)
            self.assertEqual(transformers.UMT5EncoderModel.from_pretrained.call_count, 2)

    def test_render_skips_encoder_and_moves_both_embeddings(self):
        torch, transformers, diffusers = self.modules()
        tensors = {name: MagicMock() for name in ('prompt_embeds', 'negative_prompt_embeds')}
        torch.load.return_value = tensors
        pipe = diffusers.WanPipeline.from_pretrained.return_value
        pipe.transformer.dtype = 'fp16'
        utils = SimpleNamespace(export_to_video=lambda frames, path, fps: Path(path).write_bytes(b'video-test'))
        plan = {'style':'Realistic', 'characters':'Blue shirt'}
        shot = {'prompt':'A father waves.'}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'LOW_MEMORY':'1','CPU_OFFLOAD':'1'}), patch.dict('sys.modules', {'torch':torch, 'diffusers':diffusers, 'diffusers.utils':utils}):
            renderer = WanRenderer()
            path = Path(temp) / 'episode-01' / '01-01-native.mp4'
            path.parent.mkdir()
            cache = renderer.embedding_path(renderer.prompt(shot, plan), PROFILE, temp)
            cache.parent.mkdir()
            cache.write_bytes(b'cached-test')
            renderer.render(shot, plan, path, PROFILE, 42, 40)
            self.assertIsNone(diffusers.WanPipeline.from_pretrained.call_args.kwargs['text_encoder'])
            for tensor in tensors.values():
                tensor.to.assert_called_once_with(device='cuda', dtype='fp16')
            self.assertNotIn('prompt', pipe.call_args.kwargs)
            pipe.maybe_free_model_hooks.assert_called_once()
            self.assertTrue(path.is_file())
