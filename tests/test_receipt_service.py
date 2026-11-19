"""仅验证本地回执服务契约，不验证业务付款或统计发布。"""
import http.client,json,pathlib,tempfile,threading,unittest
from support.receipt_service import ReceiptStore,create_server

class ReceiptServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=ReceiptStore(pathlib.Path(self.temp.name)/'sink.sqlite3')
        self.server=create_server(('127.0.0.1',0),self.store)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.thread.join();self.server.server_close();self.temp.cleanup()
    def request(self,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request(method,path,json.dumps(body) if body is not None else None,headers or {})
            response=conn.getresponse();return response.status,json.loads(response.read())
        finally:conn.close()
    def test_identity_and_content_are_bound(self):
        body={'receipt_id':'one','payload':{'amount':12}}
        self.assertEqual(self.request('POST','/receipts',body)[0],201)
        self.assertEqual(self.request('POST','/receipts',body)[0],200)
        body['payload']['amount']=13
        self.assertEqual(self.request('POST','/receipts',body)[0],409)
        self.assertEqual(self.request('GET','/receipts/one')[1]['payload'],{'amount':12})
    def test_lost_reply_preserves_acceptance(self):
        body={'receipt_id':'lost','payload':{'period':'2026-09'}}
        with self.assertRaises(http.client.RemoteDisconnected):
            self.request('POST','/receipts',body,{'X-Reply-Mode':'drop_once'})
        self.assertTrue(self.request('GET','/receipts/lost')[1]['accepted'])
        self.assertEqual(self.request('POST','/receipts',body,{'X-Reply-Mode':'drop_once'})[0],200)

if __name__=='__main__':unittest.main()
