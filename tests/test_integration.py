"""离线 ASGI 回归；临时文件隔离账号，所有外部 HTTP 均禁止或 mock。"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from fastapi.testclient import TestClient
from backend import server


class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.data).encode()


class OfflineAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(server, key, str(root / name)) for key,name in {
            'CONFIG_DIR':'google','TOKENS_FILE':'google/tokens.json','CACHE_FILE':'google/cache.json',
            'CHATGPT_CONFIG_DIR':'codex','CHATGPT_TOKENS_FILE':'codex/tokens.json','CHATGPT_CACHE_FILE':'codex/cache.json'
        }.items()]
        for item in self.patches: item.start()
        (root/'google').mkdir(); (root/'codex').mkdir()
        self.network = patch.object(server.urllib.request, 'urlopen', side_effect=AssertionError('禁止真实网络请求'))
        self.network.start()
        server.PKCE_CACHE.clear(); server.GOOGLE_PKCE_CACHE.clear()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close(); self.network.stop()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def test_health_and_empty_accounts(self):
        self.assertEqual(self.client.get('/api/health').status_code,200)
        self.assertEqual(self.client.get('/api/chatgpt/quota').json(),[])
        self.assertEqual(self.client.get('/api/quota').json(),[])

    def test_codex_account_add_list_switch_delete(self):
        for email in ['first@example.invalid','second@example.invalid']:
            response=self.client.post('/api/chatgpt/save-session',json={'raw_data':json.dumps({'accessToken':'FAKE-OFFLINE-ACCESS','refreshToken':'FAKE-OFFLINE-REFRESH','user':{'email':email}})})
            self.assertEqual(response.status_code,200)
        listing=self.client.get('/api/chatgpt/accounts')
        self.assertEqual(len(listing.json()['accounts']),2)
        self.assertNotIn('FAKE-OFFLINE',listing.text)
        self.assertEqual(self.client.post('/api/chatgpt/accounts/switch',json={'email':'second@example.invalid'}).status_code,200)
        self.assertEqual(self.client.post('/api/chatgpt/accounts/switch',json={'email':'missing@example.invalid'}).status_code,404)
        self.assertEqual(self.client.delete('/api/chatgpt/accounts/second@example.invalid').status_code,200)
        self.assertEqual(self.client.get('/api/chatgpt/accounts').json()['active_email'],'first@example.invalid')

    def test_codex_oauth_state_strict_match_and_replay(self):
        login=self.client.get('/api/chatgpt/login-url').json()
        state=login['state']
        self.assertEqual(parse_qs(urlparse(login['auth_url']).query)['code_challenge_method'],['S256'])
        bad=self.client.post('/api/chatgpt/complete-oauth',json={'url':'http://localhost:1455/auth/callback?code=FAKE-OFFLINE-CODE&state=unknown','state':state})
        self.assertEqual(bad.status_code,400)
        self.assertIn(state,server.PKCE_CACHE)
        with patch.object(server.urllib.request,'urlopen',return_value=Response({'access_token':'FAKE-OFFLINE-TOKEN','refresh_token':'FAKE-OFFLINE-REFRESH','expires_in':3600})), patch.object(server,'extract_chatgpt_account_info',return_value={'email':'oauth@example.invalid'}):
            request={'url':f'http://localhost:1455/auth/callback?code=FAKE-OFFLINE-CODE&state={state}','state':state}
            result=self.client.post('/api/chatgpt/complete-oauth',json=request)
            self.assertEqual(result.status_code,200)
            self.assertNotIn('FAKE-OFFLINE',result.text)
            self.assertEqual(self.client.post('/api/chatgpt/complete-oauth',json=request).status_code,400)

    def test_google_oauth_state_and_account_regression(self):
        login=self.client.get('/api/login-url').json()
        state=parse_qs(urlparse(login['login_url']).query)['state'][0]
        bad=self.client.post('/api/complete-login',json={'url':'http://127.0.0.1:8085/callback?code=FAKE-OFFLINE-CODE&state=wrong'})
        self.assertEqual(bad.status_code,400)
        self.assertIn(state,server.GOOGLE_PKCE_CACHE)
        with patch.object(server.urllib.request,'urlopen',side_effect=[Response({'access_token':'FAKE-OFFLINE-TOKEN','refresh_token':'FAKE-OFFLINE-REFRESH','expires_in':3600}),Response({'email':'google@example.invalid'})]):
            result=self.client.post('/api/complete-login',json={'url':f'http://127.0.0.1:8085/callback?code=FAKE-OFFLINE-CODE&state={state}'})
        self.assertEqual(result.status_code,200)
        status=self.client.get('/api/status')
        self.assertNotIn('FAKE-OFFLINE',status.text)
        self.assertEqual(status.json()['accounts'][0]['email'],'google@example.invalid')
        self.assertEqual(self.client.post('/api/accounts/switch',json={'email':'google@example.invalid'}).status_code,200)
        self.assertEqual(self.client.delete('/api/accounts/google@example.invalid').status_code,200)
        self.assertEqual(self.client.get('/api/status').json()['accounts'],[])

    def test_expired_states_rejected_without_network(self):
        server.PKCE_CACHE['expired']=('verifier',time.time()-1900)
        server.GOOGLE_PKCE_CACHE['expired']=('verifier',time.time()-1900)
        self.assertEqual(self.client.post('/api/chatgpt/complete-oauth',json={'url':'http://localhost:1455/auth/callback?code=FAKE-OFFLINE&state=expired'}).status_code,400)
        self.assertEqual(self.client.post('/api/complete-login',json={'url':'http://127.0.0.1:8085/callback?code=FAKE-OFFLINE&state=expired'}).status_code,400)


if __name__ == '__main__': unittest.main()
