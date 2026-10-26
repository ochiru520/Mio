from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import model_registry as registry
from app.llm import CompletionRoute, ModelRequestError, _stream_failure
from app.model_input_errors import image_input_unsupported
from app.routes.models import ModelProfileRequest, ProviderModelRequest


class ImageInputDefaultTests(unittest.TestCase):
    def test_request_and_profile_defaults_accept_images(self):
        self.assertTrue(ProviderModelRequest(model='synthetic').supports_vision)
        self.assertTrue(ModelProfileRequest(provider_name='test', model='synthetic', base_url='https://example.test').supports_vision)
        self.assertTrue(registry.ModelProfile(id='test', provider_name='test', display_name='test', model='synthetic', base_urls=(), api_key='').supports_vision)
        self.assertTrue(registry._inferred_vision_support('unknown-model'))

    def test_legacy_false_and_new_false_records_no_longer_block_images(self):
        with tempfile.TemporaryDirectory() as temp:
            original = registry.settings.model_profiles_path
            object.__setattr__(registry.settings, 'model_profiles_path', Path(temp) / 'models.json')
            try:
                _, profiles = registry.save_custom_provider(dict(provider_name='synthetic', provider_kind='relay',
                    provider_protocol='openai', base_url='https://example.test/v1', api_key='fake'),
                    [dict(model='synthetic', supports_vision=False)])
                self.assertTrue(profiles[0].supports_vision)
                document = json.loads(registry.settings.model_profiles_path.read_text('utf-8'))
                document['models'][0]['supports_vision'] = False
                registry.settings.model_profiles_path.write_text(json.dumps(document), encoding='utf-8')
                self.assertTrue(registry._custom_profiles()[0].supports_vision)
            finally:
                object.__setattr__(registry.settings, 'model_profiles_path', original)

    def test_explicit_image_capability_rejection_has_actionable_public_error(self):
        profile = registry.ModelProfile(id='test', provider_name='合成供应商', display_name='test', model='text-only', base_urls=(), api_key='')
        for message in ('This model does not support image inputs', 'Image input is not supported',
                        'image_url is only supported by certain models', '当前模型不支持图片输入'):
            with self.subTest(message=message):
                error = ModelRequestError('request failed', profile=profile, http_status=400, upstream_message=message)
                detail = error.public_detail()
                self.assertEqual(detail['code'], 'model_image_input_unsupported')
                self.assertIn('移除图片', detail['message'])
                self.assertEqual(detail['upstream_message'], message)

    def test_streamed_capability_rejection_without_http_error_status(self):
        profile = registry.ModelProfile(id='test', provider_name='合成', display_name='test', model='text-only', base_urls=(), api_key='')
        error = _stream_failure({'error': {'message': 'This model does not support images'}}, profile, 'test',
                                CompletionRoute('https://example.test', '', 'synthetic'), [])
        self.assertEqual(error.public_detail()['code'], 'model_image_input_unsupported')
        self.assertIn('支持视觉', str(error))

    def test_auth_image_format_timeout_and_tools_are_not_misreported(self):
        for status, message in ((401, 'does not support images'), (400, 'unsupported image format'),
                                (400, 'invalid image_url'), (500, 'image input not supported'),
                                (400, 'tools not supported'), (400, 'image size exceeds maximum')):
            self.assertFalse(image_input_unsupported(status, message), message)


if __name__ == '__main__':
    unittest.main()
