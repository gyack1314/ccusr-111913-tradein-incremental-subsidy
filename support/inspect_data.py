"""检查合成资料的文件格式与引用，不执行业务分析。"""
import hashlib, json, pathlib
def load(root=None):
    root=pathlib.Path(root or pathlib.Path(__file__).resolve().parents[1])
    manifest=json.loads((root/'data/manifest.json').read_text(encoding='utf-8'))
    tables={}
    for table in manifest['tables']:
        data=(root/table['path']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=table['sha256']:
            raise ValueError('资料文件摘要不匹配: '+table['name'])
        rows=[json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
        if len(rows)!=table['rows']: raise ValueError('记录数不匹配')
        keys=[row[table['key']] for row in rows]
        if len(set(keys))!=len(keys): raise ValueError('资料身份重复')
        for row in rows:
            if set(row)!=set(table['fields']): raise ValueError('字段不匹配')
        tables[table['name']]=rows
    for table in manifest['tables']:
        for column,target in table['refs'].items():
            allowed={row['id'] for row in tables[target]}
            for row in tables[table['name']]:
                if row[column] is not None and row[column] not in allowed:
                    raise ValueError('资料引用不存在: '+table['name']+'.'+column)
    return manifest,tables
if __name__=='__main__':
    m,t=load()
    print(json.dumps({'项目':m['project'],'资料版本':m['version'],'资料表':{k:len(v) for k,v in t.items()},'检查范围':'文件摘要、字段、身份与声明的引用'},ensure_ascii=False,indent=2))
