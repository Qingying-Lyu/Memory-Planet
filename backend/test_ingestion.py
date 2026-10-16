import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from .cloud import Starflow, MiMo, CloudError, model_target
from .course_schema import Evidence, restore_quote_whitespace, validate_evidence
from .wps_reader import probe, session_value, validate_url


class IngestionTests(unittest.TestCase):
    def test_cookie_file_formats_and_domain_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.txt'
            path.write_text('wps_sid=test-session;', encoding='utf-8')
            self.assertEqual(session_value(path), 'test-session')
            path.write_text('line one\nline two', encoding='utf-8')
            with self.assertRaises(ValueError):
                session_value(path)
        for url in ('http://365.kdocs.cn/l/id', 'https://365.kdocs.cn.attacker.test/l/id',
                    'https://365.kdocs.cn@attacker.test/l/id'):
            with self.assertRaises(ValueError):
                validate_url(url)

    def test_html_shell_is_not_document_success(self):
        with patch('backend.wps_reader.session_value', return_value='test-session'), \
                patch('backend.wps_reader.httpx.get', return_value=httpx.Response(200, text='<title>登录</title>')) as request:
            result = probe('https://365.kdocs.cn/l/example', 'unused')
            self.assertEqual(result['status'], 'app_shell_only')
            self.assertFalse(request.call_args.kwargs['follow_redirects'])
            self.assertNotIn('test-session', json.dumps(result))

    def test_only_whitespace_can_be_restored(self):
        items = [Evidence(block=1, quote='带宽成本降低60%左右')]
        original = '带宽成本降低\n60%\n左右。'
        repairs = restore_quote_whitespace(items, {1: original})
        self.assertEqual(len(repairs), 1)
        self.assertEqual(items[0].quote, '带宽成本降低\n60%\n左右')
        validate_evidence(items, {1: original})
        changed = [Evidence(block=1, quote='带宽成本降低80%左右')]
        self.assertEqual(restore_quote_whitespace(changed, {1: original}), [])
        with self.assertRaises(ValueError):
            validate_evidence(changed, {1: original})

    def test_mimo_through_starflow_and_truncated_output(self):
        with patch.dict('os.environ', {'ZHIWEN_MODEL_PROVIDER': 'starflow',
                                      'ZHIWEN_GENERATE_MODEL': 'mimo-v2.6-pro',
                                      'ZHIWEN_STARFLOW_KEY': 'test-key'}):
            client, model = model_target('generate', 'legacy-model')
            self.assertIsInstance(client, Starflow)
            self.assertNotIsInstance(client, MiMo)
            self.assertEqual(model, 'mimo-v2.6-pro')
            self.assertEqual(client.endpoint, 'https://kspmas.ksyun.com/v1')
            self.assertEqual(client.request_body('mimo-v2.6-flash','JSON',{})['thinking'],{'type':'disabled'})
            with patch('backend.cloud.httpx.Client') as http:
                http.return_value.__enter__.return_value.post.return_value = httpx.Response(200, json={
                    'choices': [{'finish_reason': 'length', 'message': {'content': '{"ok":true}'}}]})
                with self.assertRaises(CloudError):
                    client.json(model, '只输出JSON', {})


if __name__ == '__main__':
    unittest.main()
