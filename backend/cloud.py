"""Approved cloud endpoints only; never log credentials or provider response bodies."""
import base64
import email.utils
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path

import httpx


class CloudError(RuntimeError):
    pass


def read_secret_file(path):
    data = Path(path).read_bytes()
    from .credentials import PREFIX,unseal
    if data.startswith(PREFIX):return unseal(data)
    for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
        try:
            return data.decode(encoding).strip()
        except UnicodeError:
            pass
    raise CloudError('密钥文件编码无法识别')


def starflow_key():
    key = os.environ.get('ZHIWEN_STARFLOW_KEY') or read_secret_file(os.environ['ZHIWEN_STARFLOW_KEY_FILE'])
    if not key or '*' in key or any(c.isspace() for c in key):
        raise CloudError('星流密钥格式不正确')
    return key


class Starflow:
    endpoint = 'https://kspmas.ksyun.com/v1'
    read_timeout = 600

    def __init__(self):
        self.key = starflow_key()

    def request_body(self, model, system, data):
        body = {'model': model, 'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
            'response_format': {'type': 'json_object'},
            'max_tokens': 24000 if model.startswith('mimo-') else 12000}
        if model == 'mimo-v2.6-flash':
            body['thinking'] = {'type': 'disabled'}
        return body

    def json(self, model, system, data):
        start = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(self.read_timeout, connect=20), follow_redirects=False) as client:
                response = client.post(self.endpoint + '/chat/completions', headers={
                    'Authorization': 'Bearer ' + self.key,
                }, json=self.request_body(model, system, data))
            if response.status_code != 200:
                raise CloudError(f'模型 {model} HTTP {response.status_code}')
            result = response.json()
            choice = result['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise CloudError('模型输出未完整结束，不接受截断结果')
            value = json.loads(choice['message']['content'])
            if not isinstance(value, dict):
                raise ValueError()
            return value, {'model': model, 'provider': self.__class__.__name__, 'usage': result.get('usage', {}),
                           'seconds': round(time.monotonic() - start, 2)}
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as error:
            raise CloudError(f'模型调用或结构化输出失败：{type(error).__name__}') from None


class ParsingStarflow(Starflow):
    """Bound small parsing calls; retry only transient transport/provider failures."""
    read_timeout=120
    output_tokens=12000
    transport_attempts=2
    def request_body(self,model,system,data):
        body=super().request_body(model,system,data)
        body['max_tokens']=self.output_tokens
        if model=='glm-5.3-flash':body['thinking']={'type':'disabled'}
        return body
    def json(self,model,system,data):
        for attempt in range(self.transport_attempts):
            try:return super().json(model,system,data)
            except CloudError as error:
                message=str(error)
                retryable=any(x in message for x in ('Timeout','ConnectError','ReadError','HTTP 429','HTTP 500','HTTP 502','HTTP 503','HTTP 504','JSONDecodeError'))
                if not retryable or attempt+1>=self.transport_attempts:raise
                time.sleep(1)


class MiMo(Starflow):
    endpoint = 'https://api.xiaomimimo.com/v1'

    def __init__(self):
        path = os.environ.get('ZHIWEN_MIMO_KEY_FILE')
        self.key = os.environ.get('ZHIWEN_MIMO_KEY') or (read_secret_file(path) if path else '')
        if not self.key or '*' in self.key or any(c.isspace() for c in self.key):
            raise CloudError('请配置小米MiMo API Key文件；不能使用星流Key代替')

    def request_body(self, model, system, data):
        body = super().request_body(model, system, data)
        body.pop('max_tokens')
        body['max_completion_tokens'] = 24000
        body['thinking'] = {'type': 'enabled'}
        return body


class StarflowVision(Starflow):
    """Images go to the approved Starflow endpoint; local filenames never leave the PC."""
    def request_body(self, model, system, data):
        body = super().request_body(model, system, data['payload'])
        body['messages'][1]['content'] = [
            {'type': 'text', 'text': json.dumps(data['payload'], ensure_ascii=False)},
            {'type': 'image_url', 'image_url': {'url': data['image']}}]
        return body


class ParsingStarflowVision(ParsingStarflow):
    def request_body(self,model,system,data):
        body=super().request_body(model,system,data['payload'])
        body['messages'][1]['content']=[{'type':'text','text':json.dumps(data['payload'],ensure_ascii=False)},
            {'type':'image_url','image_url':{'url':data['image']}}]
        return body


def model_target(stage, legacy_model):
    provider = os.environ.get('ZHIWEN_MODEL_PROVIDER', 'starflow')
    if provider == 'starflow':
        return Starflow(), os.environ.get('ZHIWEN_' + stage.upper() + '_MODEL', legacy_model)
    if provider == 'mimo':
        return MiMo(), os.environ.get('ZHIWEN_MIMO_' + stage.upper() + '_MODEL', 'mimo-v2.6-pro')
    raise CloudError('未知模型服务，不自动切换资料发送目的地')


class KS3:
    """Small-file archive adapter using documented KS3 V2 signing over HTTPS."""
    def __init__(self):
        raw = read_secret_file(os.environ['ZHIWEN_KS3_CREDENTIALS_FILE'])
        credentials = {}
        for line in raw.splitlines():
            match = re.match(r'\s*([A-Za-z0-9_ ]+)\s*[:：=]\s*(.*?)\s*$', line)
            if not match:
                continue
            name = re.sub('[^a-z]', '', match[1].lower())
            if name in ('ksaccesskeyid', 'accesskeyid', 'accesskey', 'ak'):
                credentials['ak'] = match[2].strip().strip('\"\'')
            if name in ('ksaccesskeysecret', 'accesskeysecret', 'secretaccesskey', 'secretkey', 'sk'):
                credentials['sk'] = match[2].strip().strip('\"\'')
        if set(credentials) != {'ak', 'sk'} or any(not v or '*' in v or any(c.isspace() for c in v) for v in credentials.values()):
            raise CloudError('KS3凭证格式不正确')
        self.ak, self.sk = credentials['ak'], credentials['sk']
        self.bucket = os.environ.get('ZHIWEN_KS3_BUCKET', '')
        if not re.fullmatch(r'[a-z0-9-]+', self.bucket):
            raise CloudError('Bucket格式不正确')

    def put_verified(self, key, content):
        if not re.fullmatch(r'[a-zA-Z0-9/_\-.]+', key) or '..' in key:
            raise CloudError('Object key格式不正确')
        digest = hashlib.sha256(content).hexdigest()
        self._request('PUT', key, content)
        downloaded = self._request('GET', key)
        if hashlib.sha256(downloaded).hexdigest() != digest:
            raise CloudError('KS3读回校验不一致')
        return {'bucket': self.bucket, 'key': key, 'sha256': digest, 'bytes': len(content)}

    def _request(self, method, key, content=None):
        date = email.utils.formatdate(usegmt=True)
        content_type = 'application/octet-stream' if content is not None else ''
        canonical = f'{method}\n\n{content_type}\n{date}\n/{self.bucket}/{key}'
        signature = base64.b64encode(hmac.new(self.sk.encode(), canonical.encode(), hashlib.sha1).digest()).decode()
        headers = {'Date': date, 'Authorization': f'KSS {self.ak}:{signature}'}
        if content_type:
            headers['Content-Type'] = content_type
        try:
            response = httpx.request(method, f'https://{self.bucket}.ks3-cn-beijing.ksyuncs.com/{key}',
                                     headers=headers, content=content, timeout=60, follow_redirects=False)
        except httpx.HTTPError as error:
            raise CloudError(f'KS3连接失败：{type(error).__name__}') from None
        if response.status_code not in (200, 201, 204):
            raise CloudError(f'KS3 HTTP {response.status_code}')
        return response.content
