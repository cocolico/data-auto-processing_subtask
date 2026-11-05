"""Read-only loopback viewer, with byte ranges for video seeking."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
ROOT=Path((Path(__file__).resolve().parents[1]/'outputs/annotation-smoke/latest-trial.txt').read_text().strip())
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  name=self.path.split('?')[0].lstrip('/') or 'review.html'
  types={'review.html':'text/html; charset=utf-8','labels.json':'application/json','episode_000000_cam0.mp4':'video/mp4'}
  if name not in types:self.send_error(404);return
  p=ROOT/name
  if not p.is_file():self.send_error(404);return
  size=p.stat().st_size;start=0;end=size-1;partial=False
  if self.headers.get('Range'):
   import re
   m=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
   if not m:self.send_error(416);return
   start=int(m[1]);end=min(int(m[2]) if m[2] else end,end);partial=True
   if start>end:self.send_error(416);return
  self.send_response(206 if partial else 200)
  self.send_header('Content-Type',types[name]);self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store')
  if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
  self.end_headers()
  try:
   with p.open('rb') as f:
    f.seek(start);remaining=end-start+1
    while remaining:
     chunk=f.read(min(1048576,remaining))
     if not chunk:break
     self.wfile.write(chunk);remaining-=len(chunk)
  except (BrokenPipeError,ConnectionResetError):pass
if __name__=='__main__':
 print('http://127.0.0.1:8877/',flush=True)
 ThreadingHTTPServer(('127.0.0.1',8877),Handler).serve_forever()
