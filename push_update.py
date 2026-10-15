# -*- coding: utf-8 -*-
"""一键把代码更新到 GitHub（Render 会自动检测并重新部署）。

用法：
  1) 首次：把 GitHub token 写进同目录的 .gh_token 文件（或设环境变量 GH_TOKEN）
  2) 运行：python push_update.py
     可选：python push_update.py "本次更新说明"
"""
import os
import sys
import json
import base64
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
OWNER = 'jiuanli-lt'
REPO = 'costmem'
BRANCH = 'main'
API = 'https://api.github.com'

# 需要同步到 GitHub 的文件（不要放 data/ 本地数据库）
FILES = [
    'server.py',
    'cost_items.json',
    'public/index.html',
    'public/app.js',
    'public/style.css',
    'public/manifest.json',
    'public/sw.js',
    'public/icon.svg',
    'import_prices.py',
]


def get_token():
    tok = os.environ.get('GH_TOKEN')
    if tok:
        return tok.strip()
    p = os.path.join(BASE, '.gh_token')
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            return f.read().strip()
    return None


def get_sha(path, token):
    url = '%s/repos/%s/%s/contents/%s?ref=%s' % (API, OWNER, REPO, path, BRANCH)
    req = urllib.request.Request(url, headers={
        'Authorization': 'token ' + token, 'User-Agent': 'costmem-push'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8')).get('sha')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def put_file(path, token, message):
    full = os.path.join(BASE, path)
    if not os.path.isfile(full):
        print('  [跳过] %s（本地不存在）' % path)
        return None
    with open(full, 'rb') as f:
        raw = f.read()
    sha = get_sha(path, token)
    body = {
        'message': message,
        'content': base64.b64encode(raw).decode('ascii'),
        'branch': BRANCH,
    }
    if sha:
        body['sha'] = sha
    url = '%s/repos/%s/%s/contents/%s' % (API, OWNER, REPO, path)
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='PUT', headers={
        'Authorization': 'token ' + token,
        'Content-Type': 'application/json',
        'User-Agent': 'costmem-push',
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode('utf-8'))
        return res['commit']['sha'][:7]


def main():
    token = get_token()
    if not token:
        print('缺少 token：请把 GitHub token 写进 %s 或设置环境变量 GH_TOKEN'
              % os.path.join(BASE, '.gh_token'))
        return 1
    message = sys.argv[1] if len(sys.argv) > 1 else 'update from local'
    print('目标仓库：%s/%s  分支：%s' % (OWNER, REPO, BRANCH))
    ok = 0
    for path in FILES:
        try:
            sha = put_file(path, token, message)
            if sha:
                print('  [已更新] %s  -> commit %s' % (path, sha))
                ok += 1
        except urllib.error.HTTPError as e:
            print('  [失败] %s  HTTP %s  %s' % (path, e.code, e.read().decode('utf-8', 'ignore')[:120]))
        except Exception as e:
            print('  [失败] %s  %s' % (path, e))
    print('完成：成功 %d / %d 个文件。Render 会自动重新部署。' % (ok, len(FILES)))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
