"""本地投递回执演练服务，只保存提交内容，不判断业务资格。"""
from __future__ import annotations
import argparse, hashlib, json, pathlib, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

class ReceiptStore:
    def __init__(self, path):
        self.path=str(path)
        pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as c:
            c.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,digest TEXT NOT NULL,body TEXT NOT NULL,dropped INTEGER NOT NULL DEFAULT 0)')
    def connection(self):
        return sqlite3.connect(self.path,timeout=15)
    def get(self, key):
        with self.connection() as c:
            row=c.execute('SELECT digest,body FROM receipts WHERE id=?',(key,)).fetchone()
        return None if row is None else {'receipt_id':key,'digest':row[0],'payload':json.loads(row[1]),'accepted':True}
    def put(self, key, payload, drop_once=False):
        body=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        digest=hashlib.sha256(body.encode()).hexdigest()
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT digest,dropped FROM receipts WHERE id=?',(key,)).fetchone()
            if row and row[0]!=digest:return 409,False,{'error':'同一投递身份已对应不同内容'}
            exists=bool(row)
            if not row:c.execute('INSERT INTO receipts(id,digest,body) VALUES(?,?,?)',(key,digest,body))
            drop=drop_once and (not row or not row[1])
            if drop:c.execute('UPDATE receipts SET dropped=1 WHERE id=?',(key,))
        return (200 if exists else 201),drop,{'receipt_id':key,'digest':digest,'accepted':True}

def create_server(address, store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,payload):
            body=json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            prefix='/receipts/'
            if not self.path.startswith(prefix):return self.reply(404,{'error':'未知路径'})
            value=store.get(unquote(self.path[len(prefix):]))
            return self.reply(200,value) if value else self.reply(404,{'accepted':False})
        def do_POST(self):
            if self.path!='/receipts':return self.reply(404,{'error':'未知路径'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1048576:raise ValueError()
                obj=json.loads(self.rfile.read(size))
                key=obj['receipt_id'];payload=obj['payload']
                if not isinstance(key,str) or not key or len(key)>200:raise ValueError()
            except (ValueError,KeyError,TypeError):return self.reply(400,{'error':'无效投递内容'})
            status,drop,value=store.put(key,payload,self.headers.get('X-Reply-Mode')=='drop_once')
            if drop:
                self.close_connection=True
                return
            self.reply(status,value)
    return ThreadingHTTPServer(address,Handler)

if __name__=='__main__':
    p=argparse.ArgumentParser(description='本地投递回执演练服务')
    p.add_argument('--state',default='.local/receipts.sqlite3');p.add_argument('--port',type=int,default=8096)
    args=p.parse_args();server=create_server(('127.0.0.1',args.port),ReceiptStore(args.state))
    print('本地回执服务已启动，端口 '+str(server.server_port),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
