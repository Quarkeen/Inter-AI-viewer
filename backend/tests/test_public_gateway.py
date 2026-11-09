import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from starlette.testclient import TestClient
from public_gateway import make_gateway, MAX_BODY
from public_origin import active_public_origin


class PublicGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dist = Path(self.temporary.name)
        (self.dist / 'assets').mkdir(); (self.dist / 'preflight').mkdir()
        (self.dist / 'index.html').write_text('<html>candidate bundle</html>')
        (self.dist / 'assets/app.js').write_text('const API_BASE="";')
        (self.dist / 'preflight/model.task').write_bytes(b'local-model')
        self.forwarded = []

    def upstream(self, request):
        self.forwarded.append(request)
        return httpx.Response(200, json={'status': 'ok'})

    def client(self):
        return TestClient(make_gateway(self.dist, transport=httpx.MockTransport(self.upstream)))

    def test_interview_and_local_assets_are_available_without_a_localhost_api(self):
        with self.client() as client:
            self.assertIn('personal interview link',client.get('/').text)
            self.assertEqual(client.get('/interview?token='+'a'*32).text,'<html>candidate bundle</html>')
            self.assertEqual(client.get('/interview').status_code,400)
            self.assertEqual(client.get('/assets/app.js').status_code,200)
            self.assertEqual(client.get('/preflight/model.task').content,b'local-model')

    def test_hr_apis_admin_pages_secrets_and_docs_are_not_public(self):
        with self.client() as client:
            for path in ('/api/jobs','/api/applications','/api/reports/finished','/api/interviews','/api/interview-templates','/api/reports/audio','/api/shortlisted','/applications','/schedule','/candidate-report','/.env','/backend/.env','/docs','/openapi.json','/assets/../../backend/.env'):
                with self.subTest(path=path):self.assertIn(client.get(path).status_code,(404,400))
            self.assertEqual(client.post('/api/interviews/1/send-invitation').status_code,404)
            self.assertEqual(client.post('/api/scheduling/automatic/preview').status_code,404)
            self.assertEqual(client.post('/api/scheduling/automatic/commit').status_code,404)
            self.assertEqual(client.post('/api/interview/state').status_code,404)
        self.assertEqual(self.forwarded,[])

    def test_candidate_requests_preserve_token_query_multipart_bytes_and_methods(self):
        with self.client() as client:
            response=client.get('/api/interview/state',params={'token':'private-token'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.forwarded[-1].url.params['token'],'private-token')
            body=b'--boundary\r\nContent-Disposition: form-data; name="token"\r\n\r\nprivate-token\r\n--boundary--\r\n'
            response=client.post('/api/interview/preflight',content=body,headers={'Content-Type':'multipart/form-data; boundary=boundary','Authorization':'must-not-forward'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.forwarded[-1].content,body)
            self.assertNotIn('authorization',self.forwarded[-1].headers)
            self.assertEqual(response.headers['referrer-policy'],'no-referrer')
            self.assertIn('no-store',response.headers['cache-control'])

    def test_large_uploads_are_rejected_before_forwarding(self):
        with self.client() as client:
            response=client.post('/api/interview/advance',content=b'x',headers={'Content-Length':str(MAX_BODY+1)})
            self.assertEqual(response.status_code,413)
        self.assertEqual(self.forwarded,[])

    def test_backend_failure_is_an_actionable_error_without_leaking_details(self):
        def unavailable(request):raise httpx.ConnectError('private internal details',request=request)
        self.upstream=unavailable
        with self.client() as client:
            response=client.get('/api/interview/state?token=example')
            self.assertEqual(response.status_code,503)
            self.assertNotIn('private internal details',response.text)

    def test_public_origin_requires_a_live_matching_tunnel(self):
        state=self.dist/'state.json'
        state.write_text(json.dumps({'public_url':'https://example-demo.trycloudflare.com','tunnel':{'pid':123,'identity':'matching'},'gateway':{'pid':456,'identity':'matching'}}))
        with patch('public_origin.process_identity',return_value='matching'):
            self.assertEqual(active_public_origin(state),'https://example-demo.trycloudflare.com')
        with patch('public_origin.process_identity',return_value='different'):
            self.assertIsNone(active_public_origin(state))
            with self.assertRaisesRegex(ValueError,'not ready'):active_public_origin(state,require_ready=True)
        state.write_text(json.dumps({'public_url':'https://untrusted.example','tunnel':{'pid':123,'identity':'matching'}}))
        self.assertIsNone(active_public_origin(state))

    def test_invitation_origin_changes_without_modifying_mail_credentials(self):
        import api
        with patch('api.active_public_origin',return_value='https://public-demo.trycloudflare.com'):
            self.assertEqual(api._smtp_settings()['APP_BASE_URL'],'https://public-demo.trycloudflare.com')
        with patch('api.active_public_origin',return_value=None),patch.dict('os.environ',{'APP_BASE_URL':'http://localhost:5173'}):
            self.assertEqual(api._smtp_settings()['APP_BASE_URL'],'http://localhost:5173')

    def test_long_interview_processing_leaves_health_requests_responsive(self):
        import api
        from fastapi.testclient import TestClient
        entered=threading.Event();release=threading.Event()
        def slow(*args,**kwargs):
            entered.set()
            if not release.wait(5):raise RuntimeError('test timeout')
        with patch('api.continue_interview',side_effect=slow),patch('api._interview_model',return_value=lambda *args:None),patch('api._interview_state',return_value={'completion_message':'fixture'}):
            with TestClient(api.app) as client, ThreadPoolExecutor(max_workers=2) as pool:
                advance=pool.submit(client.post,'/api/interview/advance',data={'token':'fixture-token'})
                self.assertTrue(entered.wait(2))
                try:
                    health=pool.submit(client.get,'/api/health')
                    self.assertEqual(health.result(timeout=1).status_code,200)
                finally:release.set()
                self.assertEqual(advance.result(timeout=3).status_code,200)
