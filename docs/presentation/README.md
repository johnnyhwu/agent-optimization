# Skill Studio 內部分享簡報

30 頁，講 26 頁（最後 4 頁是 References 與附錄，留給 Q&A），約 25–30 分鐘。
**投影片內容是英文，講者備忘是中文**（在 PowerPoint 的「備忘稿」窗格，不會投出去）。

| 檔案 | 用途 |
|---|---|
| `Skill-Studio.pptx` | **正本**。要臨時改字、改版面，直接改這份 |
| `Skill-Studio.pdf` | 同一份的 PDF，手機上看、或寄給自己用 |
| `build_pptx.py` | 從頭重建 pptx 的腳本（內容與版面都在這裡） |
| `deck_theme.py` | 設計系統：色票、字級、邊界、所有排版 helper |
| `deck_diagrams.py` | 三張圖（迴圈、系統架構、五格管線），用**原生圖形**畫的，不是圖片 |
| `check_layout.py` | 版面檢查：文字溢出框、字級低於下限、每頁字數、文字框互相疊到 |
| `fig/` | 論文的圖表（figure1–4、table1、table3、table4）|

## 改完之後一定要跑的兩件事

```bash
python3 check_layout.py        # 沒有輸出 "clean" 就是有版面問題
soffice --headless --convert-to pdf Skill-Studio.pptx    # 重新產生 PDF
```

`check_layout.py` 用 Liberation Sans 的實際字寬（跟 Arial metric 相容）重新排版每一個
文字框，算出它「真正需要多高」再跟框高比對 —— 這是第一版最常出問題的地方
（文字跑出框、兩個框疊在一起），PowerPoint 自己不會告訴你。

## 設計系統（來自 academic-pptx-skill）

規格出自 <https://github.com/Gabberflast/academic-pptx-skill>，數值都寫在 `deck_theme.py`：

- 版面 **10 × 5.625 吋**（16:9），四周留白至少 0.5 吋
- 字體 **Arial**，全場只有這一種
- 底白；標題深藍 `1F4E79`；強調色 `2E75B6`；內文 `2D2D2D`；註解 `777777`
- **action title 26pt**（每一頁的標題都是一個完整句子，講的是結論而不是題目）
- 內文 **不小於 20pt**、每頁 **約 40 字以內**、每段 3–5 個 bullet
- 引用的圖表一律在頁底標出處，最後有 References 頁
- 結論頁是最後一張主投影片，Q&A 全程留在螢幕上；**沒有「謝謝聆聽」頁**
- 封面、分段頁、結論頁是深藍底白字

改字的時候請守住這幾條 —— 尤其是「一頁一件事、40 字、20pt」，第一版之所以看起來凌亂，
就是因為把講稿寫進了投影片。**要補充的話寫進備忘稿，不要寫進頁面。**

## 三張圖是原生圖形，不是圖片

迴圈圖、架構圖、五格管線都是 PowerPoint 的圓角矩形 + 連接線 + 文字框，可以直接拖、
直接改字。第 13–18 頁是**同一張管線圖點亮不同格**（`draw_pipeline(slide, lit=n)`），
所以要改管線的措辭，改 `deck_diagrams.py` 裡的 `STAGES` 一處就好，六頁會一起更新。

## 內容結構

| 頁 | 段落 | 時間 |
|---|---|---|
| 1–2 | 封面、agenda | |
| 3–7 | Part 1：迴圈的四個斷點、Skill Studio 各自怎麼補、系統全貌、成效與角色轉變 | 8 min |
| 8 | Part 2：切到瀏覽器實機 demo | 5 min |
| 9–24 | Part 3：SkillOpt —— 為什麼這樣框問題、五格管線逐格拆、它學出什麼、實驗結果、我們的 routing mode 為什麼偏離論文 | 8 min |
| 25–26 | 下一步、結論 | 2 min |
| 27 | References | |
| A–C | 附錄：agent server 契約、機制的 ablation、我們刻意沒做的事 | Q&A 用 |

附錄三頁是**預先準備好的 Q&A**：被問到「我要怎麼接上來」「每個機制真的都有用嗎」
「有什麼是你們沒做的」的時候直接翻過去。

## 內容出處

投影片上的說法都對得回 repo 裡的文件：

- 痛點與 Langfuse 的能 / 不能：`backend/docs/spec.md` §1.2、§1.3
- Optimize 實際做到哪裡、刻意不做什麼：`backend/docs/spec.md` §2.3a、§15.1
- 已知風險（診斷準確度未驗證、錯誤未必歸得到單一 span…）：`backend/docs/spec.md` §16
- routing mode 的三個論證：`backend/docs/routing-optimization.md`
- agent server 端要做的兩件事：`backend/docs/agent-server-api.md`
- SkillOpt 論文的中文長文：<https://datasciocean.com/paper-intro/skillopt/>

`fig/` 裡的 `table3.png`（component ablation）只有附錄 B 用到；`figure1–4`、`table1`、
`table4` 分別在第 12、19、20、21、22、23 頁。
