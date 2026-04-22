import http.server, os, sys
os.chdir('/Users/shawn.wang/Downloads/Study JSON Compare')
handler = http.server.SimpleHTTPRequestHandler
httpd = http.server.HTTPServer(('', 3456), handler)
print('Serving on port 3456', flush=True)
httpd.serve_forever()
