#!/usr/bin/env python3
"""把 skill-studio.html 打包成一個真正的單一檔案。

用法：
    python3 build_standalone.py                 # -> skill-studio-standalone.html
    python3 build_standalone.py 我的簡報.html    # 自己指定輸出檔名

所有 <img src="fig/..."> 與 <img src="assets/..."> 都會被就地換成 base64 data URI。
找不到的檔案會原樣保留 —— 投影片裡的截圖示意框會照常運作，不會壞掉。
"""
import base64, mimetypes, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
SRC  = HERE / "skill-studio.html"
OUT  = HERE / (sys.argv[1] if len(sys.argv) > 1 else "skill-studio-standalone.html")

html = SRC.read_text(encoding="utf-8")
missing, embedded = [], 0

def inline(m):
    global embedded
    rel = m.group(2)
    f = HERE / rel
    if not f.is_file():
        missing.append(rel)
        return m.group(0)
    mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    embedded += 1
    return f'{m.group(1)}data:{mime};base64,{base64.b64encode(f.read_bytes()).decode()}{m.group(3)}'

html = re.sub(r'(src=")((?:fig|assets)/[^"]+)(")', inline, html)
OUT.write_text(html, encoding="utf-8")

print(f"內嵌 {embedded} 個檔案 → {OUT.name}（{OUT.stat().st_size/1048576:.1f} MB）")
if missing:
    print("還沒放進來的檔案（投影片會顯示示意框，可以之後再補）：")
    for r in sorted(set(missing)):
        print("  -", r)
