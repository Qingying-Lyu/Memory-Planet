"""WPS documented webhook transport; no unsolicited sends during development."""
import json
import hashlib
import os
import secrets
import time
from urllib.parse import urlsplit, parse_qs

import httpx

from .cloud import CloudError, read_secret_file
from .store import connect


def webhook():
    value = read_secret_file(os.environ['ZHIWEN_WPS_WEBHOOK_FILE'])
    url = urlsplit(value)
    if (url.scheme != 'https' or url.netloc != 'xz.wps.cn' or url.path != '/api/v1/webhook/send'
            or not parse_qs(url.query).get('key') or url.fragment):
        raise ValueError('WPS webhook地址格式无效')
    return value


def send(text):
    if not text or len(text) > 5000:
        raise ValueError('消息长度无效')
    try:
        response = httpx.post(webhook(), json={'msgtype': 'markdown', 'markdown': {'text': text}},
                              timeout=20, follow_redirects=False)
        if response.status_code != 200:
            raise CloudError('WPS推送失败，待核对投递状态')
        # Business success fields must be confirmed by a real integration response.
        return {'status': 'transport_received', 'business_receipt': response.json()}
    except (httpx.HTTPError, ValueError):
        raise CloudError('WPS推送状态未知，请核对后重试') from None


def enqueue(payload):
    """Validate configured robot/group/creator before storing private work. Worker is separate."""
    expected_key = parse_qs(urlsplit(webhook()).query)['key'][0]
    if not secrets.compare_digest(str(payload.get('robot_key', '')), expected_key):
        raise PermissionError('机器人身份不匹配')
    configured_chat = os.environ.get('ZHIWEN_WPS_CHAT_ID', '')
    bindings = json.loads(read_secret_file(os.environ['ZHIWEN_WPS_BINDINGS_FILE']))
    if not configured_chat or str(payload.get('chatid')) != configured_chat:
        raise PermissionError('群未绑定')
    identity = bindings.get(str(payload.get('creator')))
    if not identity or not identity.get('owner') or not identity.get('course_id'):
        raise PermissionError('用户和课程未绑定')
    # Personal course results cannot be posted into a shared group.
    if identity.get('audience') != 'shared_training':
        raise PermissionError('个人课程需要私聊应用机器人，禁止通过群webhook回复')
    timestamp = payload.get('ctime')
    if type(timestamp) is not int or abs(time.time() - timestamp) > 300:
        raise PermissionError('回调过期或时间格式未验证')
    content = payload.get('content')
    if not isinstance(content, str) or not 1 <= len(content) <= 2000:
        raise ValueError('消息长度无效')
    raw = json.dumps({k: payload.get(k) for k in ('chatid', 'creator', 'ctime', 'content')},
                     ensure_ascii=False, sort_keys=True)
    event_id = hashlib.sha256(raw.encode()).hexdigest()
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS wps_inbox (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, course_id TEXT NOT NULL,
            content TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued')''')
        db.execute('INSERT OR IGNORE INTO wps_inbox(id,owner,course_id,content) VALUES(?,?,?,?)',
                   (event_id, identity['owner'], identity['course_id'], content))
    return {'result': 'ok'}
