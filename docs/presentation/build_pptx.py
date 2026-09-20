#!/usr/bin/env python3
"""Build Skill-Studio.pptx.

Content and structure follow github.com/Gabberflast/academic-pptx-skill:
action titles, one job per slide, ~40 words of body text, >=20pt body,
on-slide citations plus a References slide, conclusions last, appendix for Q&A.

    python3 build_pptx.py            # -> Skill-Studio.pptx
    soffice --headless --convert-to pdf Skill-Studio.pptx
"""
import pathlib, sys
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from deck_theme import *            # noqa: F403
from deck_theme import (NAVY, ACCENT, BODY, MUTED, RULE, WHITE, CALLOUT, WARM, PALE,
                        LIGHT, FONT, MONO, T_TITLE, T_SECTION, T_BODY, T_LABEL, T_CITE,
                        MARGIN, CW, BODY_Y, CITE_Y, blank, textbox, para, bullet,
                        action_title, rule, cite, header, bullets, panel, picture, notes)
from deck_diagrams import draw_loop, draw_architecture, draw_pipeline, _badge

FIG = HERE / "fig"
SRC = "Source: SkillOpt, arXiv:2605.23904"
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(10), Inches(5.625)


def content(title):
    s = blank(prs)
    action_title(s, title)
    rule(s)
    return s


# ═════════════════════════════════════════════════════════════════ 1 · title
s = blank(prs, bg=NAVY)
t = textbox(s, MARGIN, 1.70, 8.6, 1.10)
para(t.text_frame, "Skill Studio", size=40, color=WHITE, bold=True, space_after=0, first=True)
t = textbox(s, MARGIN, 2.80, 8.6, 0.40)
para(t.text_frame, "Where an agent's skills get measured, tried and trained",
     size=16, color=LIGHT, space_after=0, first=True)
rule(s, y=3.45, w=2.5, h=0.04, color=ACCENT)
t = textbox(s, MARGIN, 3.62, 8.6, 0.70)
tf = t.text_frame
para(tf, "Internal sharing · September 2026", size=15, color=LIGHT, space_after=3, first=True)
para(tf, "The studio makes them; the Skill Marketplace sells them", size=13, color=LIGHT)
notes(s, """
開場一句：這個系統是在開發內部 agent 的過程中，被我們自己的痛點逼出來的 ——
不是先有系統再找問題，是先有四個每天都在痛的地方。
時間分配先講：前面 8 分鐘講痛點、中間 5 分鐘實機 demo、後面 8 分鐘講演算法。
如果聽眾裡有人沒碰過 agent 開發，補一句：我們的 agent 是 stateless 的，每題都重新開始，
先挑一份 skill（開發者寫的 playbook），再一路 tool calling 到答案。
""")

# ══════════════════════════════════════════════════════════════ 2 · agenda
s = content("Today: the pain, the demo, and the algorithm behind Optimize")
rows = [("01", "The pain", "The loop, and where it breaks", "8 min"),
        ("02", "Demo", "The real thing, in the browser", "5 min"),
        ("03", "The algorithm", "A skill file as a trainable weight", "8 min"),
        ("+", "What's next", "Cheaper optimization, shorter skills", "2 min")]
y = 1.62
for num, head, sub, mins in rows:
    n = textbox(s, MARGIN, y + 0.02, 0.6, 0.32)
    para(n.text_frame, num, size=T_CITE, color=ACCENT, bold=True, space_after=0, first=True)
    b = textbox(s, 1.10, y - 0.04, 6.6, 0.74)
    tf = b.text_frame
    para(tf, head, size=T_BODY, color=BODY, bold=True, space_after=3, first=True)
    para(tf, sub, size=T_LABEL, color=MUTED, space_after=0)
    m = textbox(s, 7.9, y + 0.02, 1.6, 0.32)
    para(m.text_frame, mins, size=T_CITE, color=MUTED, align=PP_ALIGN.RIGHT,
         space_after=0, first=True)
    y += 0.84
cite(s, "Interrupt any time; the bigger questions keep until the end.")
notes(s, """
不要在這頁停超過 20 秒，講完就進第一部分。
提醒自己：前兩部分是鋪陳，真正的重頭戲是 demo 跟第三部分的演算法。
""")

# ═══════════════════════════════════════════════════════ 3 · the loop breaks
s = content("We run the same loop every day, and it breaks in four places")
draw_loop(s)
cite(s, "Langfuse is still the backbone our traces live in. What is missing is what it does not "
        "have and we cannot add to it: the error-localization engine, per-span information of "
        "our own, and skill optimization.")
notes(s, """
這張是第一部分的地圖，四個號碼後面那頁會一一對上。
關鍵的一句：這四件事不是四個零散的需求，是同一條迴圈上的四個斷點。
Langfuse 那句一定要唸出來 —— 在場可能有 Langfuse 的支持者。我們沒有要取代它，
trace 到今天都還存在 Langfuse、從 Langfuse 讀；我們補的是它沒有、而且我們也改不了它
source code 去補的那一段。
中間那個數字講慢一點：一個新 topic 從 0% 爬到 90% 以上，短則三天，長則兩週。
""")

# ══════════════════════════════════════════════════ 4 · what Skill Studio does
s = content("Skill Studio repairs all four breaks in one place")
cells = [(1, "Config", "Stored with every run, exactly as it ran"),
         (2, "Traces", "An LLM names the suspect span — a clue, not a verdict"),
         (3, "Fixes", "One question, an editable skill, one button"),
         (4, "Questions", "Drafted from the trace, then reviewed")]
for i, (n, head, text) in enumerate(cells):
    col, row = i % 2, i // 2
    x = MARGIN + col * 4.62
    y = 1.52 + row * 1.72
    panel(s, x, y, 4.38, 1.50, fill=PALE, line_color=RULE)
    _badge(s, x + 0.34, y + 0.35, n)
    h = textbox(s, x + 0.56, y + 0.22, 3.60, 0.32)
    para(h.text_frame, head, size=T_LABEL, color=ACCENT, bold=True,
         space_after=0, first=True)
    b = textbox(s, x + 0.22, y + 0.64, 3.94, 0.80)
    para(b.text_frame, text, size=T_BODY, color=BODY, line=1.18, space_after=0, first=True)
notes(s, """
四格照順序講，每格大約 20 秒，不要展開細節。

1 config：run 記下的是「觸發它的完整設定」，而不是對環境的 delta —— 空欄位在啟動當下
  就解析成實際值。所以三個月後打開還知道它當初跑的是什麼。
2 診斷：把開發者寫的「理想推理流程白話描述」跟實際 trace 一起交給 LLM，問兩者在哪裡分岔。
  a clue, not a verdict 這句要停一下：可以有多個嫌疑、confidence 只有三檔、還有 caveat
  可以說「這題不該歸給單一 span」。因為「錯誤可歸因到單一 span」這個假設常常是錯的
  （compounding error、好幾條都對的路徑、錯在 tool 不在 skill）。過度自信會把人帶去
  錯的方向，而且帶著假的權威。
3 Playground：一題、一份可編輯的 skill 副本、一個按鈕，跑完整 trajectory 並自動判分。
  誠實補一句：平台不保證 agent 真的吃了你的 override，UI 上直說這件事；證據是那段文字
  會出現在第一個 span 的 system message。
4 Shortlist：Draft from trace 讓 LLM 讀 trace 先寫一版 thinking process 草稿。
  預填的 expected answer 標成 unverified —— 照單全收等於斷言 agent 已經是對的，
  那題會永遠通過，永遠抓不到答案變壞。
""")

# ═══════════════════════════════════════════════════ 5 · the loop is slow
s = content("The loop itself takes 3 days to 2 weeks, so Optimize automates it")
panel(s, MARGIN, 1.52, 9.0, 1.06, fill=CALLOUT, line_color=ACCENT)
t = textbox(s, 0.78, 1.66, 8.44, 0.80, anchor=MSO_ANCHOR.MIDDLE)
para(t.text_frame, "Train a skill like a model: "
                   "epochs, steps, a learning rate, a validation gate.",
     size=T_BODY, color=NAVY, bold=True, line=1.15, space_after=0, first=True)
bullets(s, MARGIN, 2.82, 9.0, 1.90, [
    [("isolated", {"bold": True}), (" trains the body; ", {}),
     ("routing", {"bold": True}), (" trains the description", {})],
    "A chart per step, and a diff of what changed",
    "The output is a zip you install yourself",
])
cite(s, "Runs cancel and resume; four early-stop conditions keep a bad night from burning to the end.")
notes(s, """
前面四個是流程上的摩擦，這一個是本質上的慢，而且是 Part 3 的入口。
一句可以引起共鳴的話：and frankly, nobody enjoys tuning prompts.
用語刻意對齊深度學習 —— epoch、step、learning rate、validation gate ——
在場都是工程師，一聽就知道每個詞在做什麼。
zip 不自動寫回是刻意的：寫回 agent server 需要 skill 更新 API 跟版本控制 / rollback，
那是另一個題目。這點如果有人追問，附錄 C 有完整的「刻意不做」清單。
""")

# ══════════════════════════════════════════════════════ 6 · architecture
s = content("Langfuse owns traces; we own what Langfuse cannot express")
draw_architecture(s)
cite(s, "All seven seams are fake by default: the whole system runs on nothing but Docker, "
        "and one real service can be brought up at a time.")
notes(s, """
兩個重點就好，不要逐格唸。

一、七個 seam：每個外部相依都是一個 Python Protocol，各有 fake 跟 real 兩種實作，
    各自獨立切換、預設全 fake。所以整套在一台只有 Docker 的機器上就跑得起來，
    新人上手跟 demo 都靠這個。
二、分工：Langfuse 擁有 trace，我們擁有 Langfuse 表達不了的東西。span 的
    input / output / token 是看的時候才去 Langfuse 讀，從來不複製進我們的資料庫。

correlation id 是整個錯誤定位的前提：我們在呼叫前產生一個 id 放在 metadata，
agent server 必須拿它當 Langfuse 的 trace id。沒有它，平台就找不到自己剛剛造成的
那條 trace。這是唯一一件必須在我們 repo 之外改的事 —— 細節在附錄 A。
""")

# ════════════════════════════════════════════════════ 7 · where it stands
s = content("Optimize turns weeks into one night, but not into zero people")
for i, (lab, big, sub, edge) in enumerate([
        ("BEFORE", "3 days – 2 weeks", "a person editing, re-running, reading results", RULE),
        ("NOW · isolated mode", "overnight", "press it before you leave, read it in the morning", ACCENT)]):
    x = MARGIN + i * 4.62
    panel(s, x, 1.52, 4.38, 1.34, fill=WHITE if i == 0 else CALLOUT, line_color=edge)
    t = textbox(s, x + 0.24, 1.64, 3.90, 1.10)
    tf = t.text_frame
    para(tf, lab, size=12, color=MUTED if i == 0 else ACCENT, bold=True, space_after=4, first=True)
    para(tf, big, size=24, color=NAVY, bold=True, space_after=4)
    para(tf, sub, size=13, color=MUTED, line=1.12, space_after=0)
bullets(s, MARGIN, 3.08, 9.0, 1.70, [
    "In use on several BOD and PPD topics",
    "The job moves to reviewing a skill that already passed validation",
    "Missing internal domain knowledge stalls accuracy near 80%",
])
notes(s, """
數字講得保守而具體。重點不是準確率提高多少，是人的時間從哪裡挪到哪裡。
「下班前按下去、隔天早上看結果」這句最有畫面。

最後一顆 bullet 一定要唸出來，而且要補完整：optimizer 缺台積內部 domain knowledge 的
時候，準確率就是卡在 80% 左右，最後那一段還是要人補 —— 差別在於人接手的時候，手上是一份
已經通過驗證的 skill，而不是一張白紙。所以不能按下去就走人，隔天還是要有人讀那份 diff。

如果有人問「那有什麼是你們刻意沒做的」，直接跳附錄 C。
""")

# ═════════════════════════════════════════════════════════════ 8 · demo
s = blank(prs, bg=NAVY)
t = textbox(s, MARGIN, 1.30, 8.6, 0.30)
para(t.text_frame, "PART 2", size=T_LABEL, color=LIGHT, bold=True, space_after=0, first=True)
t = textbox(s, MARGIN, 1.62, 8.6, 0.90)
para(t.text_frame, "Demo", size=36, color=WHITE, bold=True, space_after=0, first=True)
rule(s, y=2.62, w=2.5, h=0.06, color=ACCENT)
b = textbox(s, MARGIN, 2.88, 8.6, 1.94)
tf = b.text_frame
for i, step in enumerate([
        "Run an eval — every question listed from the first second",
        "Open a failed one — the diagnosis jumps to the suspect span",
        "Carry it into the playground — edit the skill, ask again",
        "Shortlist it — draft the reasoning, make a new eval set",
        "Open an optimization run — the per-step chart and the diff"]):
    p = para(tf, step, size=15, color=LIGHT, space_after=6, first=(i == 0))
    bullet(p, char="–")
notes(s, """
切到瀏覽器。五分鐘，照這五步走，不要臨時發散。
如果某一步卡住（例如 agent server 慢），直接說「這步平常大概十秒」然後跳下一步，
不要在台上 debug。
""")

# ═════════════════════════════════════════════════════════ 9 · section: skillopt
s = blank(prs, bg=NAVY)
t = textbox(s, MARGIN, 1.30, 8.6, 0.30)
para(t.text_frame, "PART 3", size=T_LABEL, color=LIGHT, bold=True, space_after=0, first=True)
t = textbox(s, MARGIN, 1.62, 8.6, 0.90)
para(t.text_frame, "SkillOpt", size=36, color=WHITE, bold=True, space_after=0, first=True)
rule(s, y=2.62, w=2.5, h=0.06, color=ACCENT)
t = textbox(s, MARGIN, 2.88, 8.6, 1.16)
tf = t.text_frame
para(tf, "Treating a skill file as a trainable weight — the model is never touched",
     size=17, color=LIGHT, space_after=8, first=True)
para(tf, "arXiv:2605.23904 (Microsoft) · our implementation is vendored from "
         "microsoft/SkillOpt, with our agent and our judge swapped in",
     size=13, color=LIGHT, line=1.2)
notes(s, """
進重頭戲。一句話定調：all the learning happens in one text file, the model is never touched.
出處講清楚：微軟的論文，我們的實作 vendored 自 microsoft/SkillOpt，只有兩件事換成我們
自己的 —— 打我們的 target agent、用我們的 LLM judge 評分。反向傳播（reflect）、
gradient 聚合、learning rate 裁切、validation gate 全部是原本的。
""")

def two_col_rows(slide, x, y, w, rows, *, col1=3.1, size=T_BODY, gap=0.52, head=None):
    """A light rules-only table: one text box per cell, a hairline between rows."""
    yy = y
    if head:
        h = textbox(slide, x, yy, w, 0.28)
        para(h.text_frame, head[0], size=T_CITE, color=MUTED, bold=True, space_after=0, first=True)
        h2 = textbox(slide, x + col1, yy, w - col1, 0.28)
        para(h2.text_frame, head[1], size=T_CITE, color=MUTED, bold=True, space_after=0, first=True)
        yy += 0.34
    for left, right in rows:
        rule(slide, y=yy, x=x, w=w)
        yy += 0.10
        a = textbox(slide, x, yy, col1 - 0.18, gap)
        para(a.text_frame, left, size=size, color=BODY, space_after=0, line=1.12, first=True)
        b = textbox(slide, x + col1, yy, w - col1, gap)
        para(b.text_frame, right, size=size, color=ACCENT, bold=True, space_after=0,
             line=1.12, first=True)
        yy += gap
    return yy


def appendix(label, title):
    s = blank(prs)
    t = textbox(s, MARGIN, 0.34, CW, 0.28)
    para(t.text_frame, label, size=14, color=MUTED, italic=True, space_after=0, first=True)
    action_title(s, title, size=24, y=0.64, h=0.62)
    rule(s, y=1.34)
    return s


# ══════════════════════════════════════════ 10 · why not self-rewriting
s = content("Self-rewriting prompts overfit and forget, so SkillOpt treats the file as a weight")
header(s, MARGIN, 1.52, 4.3, "THE USUAL LOOP", color=MUTED, size=T_CITE)
bullets(s, MARGIN, 1.86, 4.3, 2.20, [
    "Overfits to whatever error just happened",
    "Bloats with one-off patches",
    "Quietly erases earlier lessons",
])
panel(s, 5.12, 1.46, 4.38, 2.74, fill=CALLOUT, line_color=ACCENT)
header(s, 5.38, 1.66, 3.9, "SKILLOPT", size=T_CITE)
bullets(s, 5.38, 2.00, 3.86, 2.00, [
    "External, versioned, trainable state",
    "Bounded, controlled steps",
    "Kept only if it passes validation",
], color=NAVY)
cite(s, "That third failure is catastrophic forgetting \u2014 in plain text instead of in weights.")
notes(s, """
先破題：為什麼不是大家最直覺的那個做法 —— 讓模型自己反思、自己改 prompt。
Dynamic Cheatsheet、Agentic Context Engineering 這類 test-time 的做法走的都是這條路。
三個問題裡第三個最值得強調：上一次學到的教訓會被這一次的補丁默默覆蓋掉，
而且你不會收到任何通知 —— 這就是發生在純文字上的災難性遺忘。
SkillOpt 的回答不是再打一個補丁，而是換框架：把技能檔案當成深度學習優化器眼中的
權重張量，受控、有界、通過驗證才保留，沒幫助就回滾。目標模型一個參數都不動。
""")

# ══════════════════════════════════════════════ 11 · the analogy table
s = content("Every mechanism has a deep-learning counterpart")
two_col_rows(s, MARGIN, 1.46, 9.0, [
    ("Parameters W", "The skill file best_skill.md"),
    ("Gradient", "Edit proposals from a minibatch"),
    ("Learning rate", "The edit budget, cosine 4 \u2192 2"),
    ("Validation checkpoint", "The blind D_sel gate, strictly greater"),
    ("Momentum / EMA", "The epoch-wise slow update"),
], head=("DEEP LEARNING", "ITS TEXT-SPACE EQUIVALENT"))
cite(s, "Each one exists to make the left-hand column work in text space.")
notes(s, """
這張是後面所有東西的地圖。對工程師聽眾來說，這張表講完，後面五個機制都有位置可以掛。
講法：右邊每一個機制存在的目的，都是為了讓左邊那一欄真的能在文字空間裡運作起來。
每一列都可以補一句「它在防什麼」：
  參數 —— 一份 300 到 2,000 token 的 Markdown。
  梯度 —— 防的是對單一軌跡的雜訊過擬合。
  學習率 —— 防的是一步邁太大、把已經運作的部分拆掉。
  驗證 checkpoint —— 防的是「看起來合理」但其實沒有幫助的修改。
  動量 —— 防的是短期補丁一點一點侵蝕長期的教訓。
如果時間緊，這頁可以講久一點、後面每格講短一點，因為有了這張表，細節會自己對號入座。
""")

# ═══════════════════════════════════════════════ 12 · two models
s = content("Separating the two models makes deployment cost zero")
picture(s, str(FIG / "figure1.png"), MARGIN, 1.46, 5.05, 3.30)
bullets(s, 5.80, 1.66, 3.70, 2.96, [
    [("Target M", {"bold": True}), (": frozen weights and prompt; only the skill file changes", {})],
    [("Optimizer O", {"bold": True}), (": offline only, reads trajectories, proposes edits", {})],
    "When training ends, its API spend disappears",
], size=18)
cite(s, "Figure 1. " + SRC)
notes(s, """
兩個模型徹底分開，是這套設計在部署階段成本為零的原因。
目標模型是凍結的，連它自己原生的 system prompt 都不動；唯一會變的是被塞進去的那份 skill。
優化器模型完全不碰任務，只讀軌跡跟分數、提出修改，而且只在離線階段運作 ——
通常是一個能力更強的前沿模型。
訓練一結束，優化器那一側的 API 花費就整個消失，上線的只有凍結模型加一份小小的文字檔。
這也是為什麼它適合我們：正式環境不會因此變慢，也不會變貴。
""")

# ═══════════════════════════════════════════ 13 · pipeline overview
s = content("One step is five stages, from evidence to gate")
draw_pipeline(s)
b = textbox(s, MARGIN, 3.18, 9.0, 0.80)
para(b.text_frame, "Gather the evidence, act, then earn it on an exam the optimizer has never seen.",
     size=T_BODY, color=BODY, space_after=0, first=True)
notes(s, """
先給全景，再一格一格拆。告訴聽眾：接下來五頁都是同一張圖，只是輪流把其中一格點亮，
不用擔心迷路。
整段的核心句就是投影片上那一句：先收證據，再動手，然後必須通過一場它沒看過的考試才算數。
""")

STAGE_SLIDES = [
    (1, "A batch of 40 separates a systematic weakness from one-off noise",
     ["React to one failure and you overfit to its noise",
      "40 samples carry enough statistical weight",
      "Split by score: failures give fixes, successes give reinforcement"],
     None,
     """
     40 是刻意的大批次。如果只對一條失敗軌跡反應，優化器會對造成那次失敗的特定雜訊過擬合
     —— 一次網路延遲、一個措辭特別奇怪的輸入 —— 而不是找出真正反覆出現的模式。
     分池也要講：失敗池找的是修復（什麼壞掉了、怎麼補），成功池找的是強化 ——
     那些已經在發揮作用、但還沒被寫進技能文件的好習慣。沒被記錄下來，下一次未必還能
     靠運氣重現。
     """),
    (2, "Minibatches of 8 find the failures that recur across tasks",
     ["One trajectory overfits; a whole pool blows the context window",
      "Eight fits, and still forces comparison across tasks",
      "Each analyst returns atomic edits anchored to exact strings"],
     "Up to 16 analyst calls run in parallel \u2014 Map, then Reduce.",
     """
     為什麼是 8：1 會重現我們剛剛才避開的單軌過擬合；把一整池二十幾條全塞進一次呼叫，
     會超出有用的上下文範圍，還會招致 lost-in-the-middle。
     8 剛好小到留在上下文限制內，又大到足以逼模型橫向比較，找出跨任務共同出現的失敗模式。
     形狀正好是 MapReduce：每個 minibatch 獨立分析（Map），建議之後再彙整（Reduce）。
     兩位分析師：analyst_error.md 看失敗，analyst_success.md 看成功，回傳的是原子操作
     —— append / insert_after / replace / delete —— 每一項都精確定位在文件中的某個目標字串。
     """),
    (3, "Merging keeps the evidence, and fixing failures always wins",
     ["Duplicates collapse to the most general wording, carrying support_count",
      "Conflicting edits are resolved here, not later"],
     None,
     """
     並行的代價是：回來的是好幾份可能重疊、有時互相矛盾的清單，必須收斂成一份。
     兩階段：池內的樹狀合併（每批最多 8 份兩兩合併），然後跨池合併。
     support_count 是很實用的設計：幾個獨立的分析師提出了等價的建議，就是這個模式
     有多常出現的粗略指標。
     跨池的規則毫不含糊：修復失敗永遠優先。同一個位置上，失敗池的版本保留，沒有例外。
     修好壞掉的東西，優先級嚴格高於強化已經運作良好的東西。
     """),
    (4, "The edit budget is a learning rate, decaying 4 to 2",
     ["Applying too many edits at once is too large a step",
      "Explore early, consolidate late",
      "Ranked by trajectories fixed, gap filled, generality, actionability"],
     None,
     """
     這是學習率在文字空間的直接對應，也是最好懂的一個類比。
     一次套用太多修改，就跟梯度下降踩了太大的步伐一樣：文件會陷入不穩定，
     丟失先前辛苦累積的教訓。
     餘弦衰減的意思：前期文件大部分還是空的，大幅度的結構性修改是安全而且有用的；
     後期文件已經有實質內容，就只該讓小幅度的用詞微調通過。
     四個排序準則裡，第一個直接掛在 support_count 上 —— 修好越多條軌跡的排越前面。
     只有前 Lt 名進得了候選技能。
     """),
    (5, "Only a strictly higher blind score is written to disk",
     ["The optimizer never sees D_sel at all",
      "A tie is discarded, not kept",
      "Rejected edits go into a buffer the next step reads"],
     "A hallucinated target string is skipped and logged, never fatal to the run.",
     """
     這是整套設計裡最重要的一項工程紀律，直接借用標準 ML 實務：資料隔離。
     優化器永遠只看得到訓練集的軌跡，完全看不到 D_sel。
     接受規則是嚴格大於 —— 平手不算。這條門檻堵住的正是迭代式提示詞編輯最常見的失敗模式：
     一連串各自看起來都合理的修改，加起來只是增加雜訊跟讓文件膨脹。
     被拒絕的修改不浪費：連同它想修的失敗模式、掉了幾分，一起注入下一步的 prompt，
     意思是「我們試過了，它傷分數，別再提一樣的建議」。
     最後那個工程保險值得講，因為在場都是工程師：LLM 偶爾會幻覺出一個文件裡根本沒有的
     目標字串，那一項就標成 skip、記進 edit_apply_report.json，同批其他修改照常套用。
     """),
]
for n, title, items, foot, note in STAGE_SLIDES:
    s = content(title)
    draw_pipeline(s, lit=n)
    bullets(s, MARGIN, 3.12, 9.0, 1.74, items)
    if foot:
        cite(s, foot)
    notes(s, note)

# ══════════════════════════════════════════════ 19 · epoch-level loop
s = content("An epoch-level loop catches what step-level edits erode")
bullets(s, MARGIN, 1.52, 4.55, 3.30, [
    "Twenty tasks, old skill against new, one fixed exam",
    "Regressions expose edits that each passed their own gate",
    "The conclusion lands in a protected block \u2014 the momentum",
    "The meta skill teaches the optimizer, never the deployed file",
], size=18)
picture(s, str(FIG / "figure2.png"), 5.30, 1.46, 4.20, 3.34)
cite(s, "Both are wired up in our implementation, and both ship disabled.  \u00b7  Figure 2. " + SRC)
notes(s, """
逐步的修改在設計上本來就是短視的 —— 每一步都只針對最近這批 40 題反應。
所以加了第二層比較慢的控制迴圈，每個 epoch 才跑一次。
慢速更新：抽 20 題，讓舊 skill 跟新 skill 跑同一份固定的考卷，這是受控的 A/B，
不是單純比原始分數。四種狀態裡「退步」最重要 —— 它是最清楚的訊號，說明最近這些
各自都通過了驗證的修改，加起來卻讓某些東西更糟。
結果寫進 SLOW_UPDATE_START 標記的受保護區塊，一般的 step 級修改在這個 epoch 剩下的
時間裡碰不到它。這就是動量。即使是它，也一樣要過 D_sel 守門。
meta skill 是另一個乾淨的元學習例子：優化器寫給自己的筆記，目標模型永遠看不到，
所以部署出去的 skill 一個 token 都不會變胖。
最後補一句我們自己的狀態：這兩項我們已經接線，但預設是關掉的。
""")

# ═════════════════════════════════════════════ 20 · what it learns
s = content("What it learns reads like a senior engineer's review comment")
picture(s, str(FIG / "figure4.png"), MARGIN, 1.46, 9.0, 2.50)
b = textbox(s, MARGIN, 4.10, 9.0, 0.82)
para(b.text_frame, "Our own developers write rules like these already \u2014 "
                   "it takes them three days to two weeks.",
     size=T_BODY, color=BODY, space_after=0, first=True)
cite(s, "Figure 4. " + SRC)
notes(s, """
這頁回答在場一定有人在想的問題：講了這麼多機制，它到底學出什麼東西？
念一兩條就好。SpreadsheetBench 那條最好懂：先檢查活頁簿的結構與公式，然後把算好的
靜態值寫進整個目標範圍，不要指望 Excel 自己重算。
這正是一個資深工程師會在 code review 裡講的那種話 —— 具體、可操作、是一條普遍規則
而不是某一題的答案。這也是為什麼前面那四個排序準則長那樣：它們就是在逼優化器寫出
這種句子。
""")

# ══════════════════════════════════════════════════ 21 · main result
s = content("SkillOpt is best or tied-best in all 52 combinations")
picture(s, str(FIG / "table1.png"), MARGIN, 1.46, 5.30, 3.32)
bullets(s, 6.10, 1.76, 3.40, 2.52, [
    "+23.5 direct chat, +24.8 Codex, +19.1 Claude Code",
    "One to four accepted edits per benchmark",
    "Ahead of TextGrad, GEPA and EvoSkill",
], size=18)
cite(s, "Table 1. " + SRC)
notes(s, """
52 個（模型、基準測試、執行環境）組合，每一個都是最佳或並列最佳。
提升幅度取決於執行環境，三個數字念一下就好。
Codex 那個數字最值得玩味：它暗示在操作工具的 agent 身上，還有大量空間不在底層模型的
能力上，而在於周邊的操作流程被講清楚了多少 —— 這正好就是我們在做的事。
「1 到 4 次被接受的修改」這件事要停一下：驗證守門把絕大多數提出的修改都擋下來了，
這種選擇性正是這種謹慎系統應該有的樣子。
""")

# ═══════════════════════════════════════════════════ 22 · transfer
s = content("A trained skill transfers, and beats training in the target harness")
picture(s, str(FIG / "table4.png"), MARGIN, 1.46, 5.30, 3.32)
bullets(s, 6.10, 1.80, 3.40, 2.40, [
    "Codex \u2192 Claude Code: 22.1 \u2192 81.8, above the 80.4 trained there",
    "No transferred cell fell below the target's own no-skill baseline",
], size=18)
cite(s, "Table 4. " + SRC)
notes(s, """
這是全篇最有說服力的一個數字。一份在 Codex 環境裡針對試算表任務訓練出來的技能，
在完全沒有進一步優化的情況下直接丟進 Claude Code，把分數從 22.1 拉到 81.8 ——
而且超過了直接在 Claude Code 環境裡訓練出來的 80.4。
這強烈暗示，優化器萃取出來的東西更接近「該怎麼思考這類任務」，而不是
「該怎麼針對這個執行環境的語法措辭」。
三個遷移軸向 —— 模型規模、執行環境、基準測試 —— 沒有任何一個遷移後的欄位，
低於目標自己的無技能基準線。
""")

# ═══════════════════════════════════════════════════════ 23 · the gate
s = content("The gate selects for generalization, not for overfit")
picture(s, str(FIG / "figure3.png"), MARGIN, 1.46, 5.60, 3.10)
bullets(s, 6.35, 1.90, 3.15, 2.30, [
    "The validation peak lines up with the test peak",
    "Demote the optimizer to the target model and 56\u201374% of the gain survives",
], size=18)
cite(s, "Figure 3. " + SRC)
notes(s, """
驗證集分數（橘線）的最高點，跟測試集分數（綠線）的最高點高度重合。
這在統計上是一個很好的訊號：代表驗證守門機制真正挑出來的，是泛化能力最好的版本，
而不是恰好在訓練批次上表現亮眼、換個題目就現形的過擬合版本。
第二點是另一篇的結果：即使把「教練」降級成跟目標模型同一個（規模小得多的）模型
—— 也就是自己優化自己 —— 有界更新加上驗證守門這套機制，依然足以挽回 56% 到 74%
的收益。對沒有前沿模型預算的團隊來說，這是很實際的結論。
""")

# ══════════════════════════════════════════════════════ 24 · routing
s = content("Routing needed a different algorithm, and the oscillation proved it")
panel(s, MARGIN, 1.50, 3.90, 2.30, fill=PALE, line_color=RULE)
t = textbox(s, 0.72, 1.70, 3.46, 1.90)
tf = t.text_frame
para(tf, "isolated", size=18, color=NAVY, bold=True, space_after=2, first=True)
para(tf, "a skill's body \u2014 thousands of words", size=15, color=BODY, space_after=14, line=1.12)
para(tf, "routing", size=18, color=NAVY, bold=True, space_after=2)
para(tf, "a skill's description \u2014 one line of YAML", size=15, color=BODY, space_after=0, line=1.12)
bullets(s, 4.76, 1.50, 4.74, 2.60, [
    "Edits are mutually exclusive; merge picks without the evidence",
    "Failure and success are one boundary; split, they oscillate",
    "Routing decides before the agent acts",
], size=18)
cite(s, "isolated is untouched, and is still the paper's algorithm. "
        "Full reasoning: backend/docs/routing-optimization.md")
notes(s, """
這頁是我們自己的判斷，不是論文的內容 —— 值得花一點時間，因為這是「我們做了什麼」的答案。
背景：routing mode 一開始就是把同一套演算法指到另一個欄位，那就是這頁記錄的錯誤。
症狀是：routing run 的分數一直動，從來不收斂。

一、兩個 routing minibatch 各自 replace 同一行，本質上互斥；而 merge 拿到的是編輯、
    不是背後的題目 —— 能決定的證據在上一階段就被丟掉了。
    改法：整批只做一次 analyst 呼叫，把選擇留在有證據的地方。
二、失敗與成功是同一個決策邊界的兩面。分開問，失敗分析師會提收窄（看不見收窄弄壞了
    什麼）、成功分析師會提放寬（看不見誤觸的那些）—— 這就是一台震盪產生器。
    改法：一起送，當成一個受約束的問題：涵蓋這些、排除那些。
三、路由決策發生在 agent 動作之前，完整觀測只有（題目、被標記的 skill、實際開啟的 skill）。
    工具目錄、對話、答案全都付了錢卻無法影響編輯。正因為觀測便宜到一行，前兩點才付得起：
    一整個訓練批次塞得進一個 prompt，比舊的八條軌跡還短。
順帶一提：routing 不送 gold answer，所以答案外洩的面在這條路上直接消失，不只是被擋住。
""")

# ═════════════════════════════════════════════════════ 25 · what's next
s = content("Two directions could make this cheaper and shorter")
panel(s, MARGIN, 1.48, 9.0, 0.80, fill=WARM, line_color=None)
t = textbox(s, 0.72, 1.56, 8.56, 0.66, anchor=MSO_ANCHOR.MIDDLE)
para(t.text_frame, "Training cost today: 0.6\u20131.1M tokens per point on short tasks, "
                   "38\u201346M on long-context ones.",
     size=17, color=BODY, space_after=0, first=True)
for i, (head_, items) in enumerate([
        ("CHEAPER", [[("SkillOpt-Lite", {"bold": True}), ("  arXiv:2607.03451", {"size": 14, "color": MUTED}),
                      ("\nA minimal pipeline; +8.8 and +25.4 on LiveMath", {})],
                     [("NPO", {"bold": True}), ("  arXiv:2608.27266", {"size": 14, "color": MUTED}),
                      ("\nMatches GEPA with fewer rollouts", {})]]),
        ("SHORTER", [[("SkillZip", {"bold": True}), ("  arXiv:2608.11079", {"size": 14, "color": MUTED}),
                      ("\nEvaluation-free compression of a skill that grew by appending", {})]])]):
    x = MARGIN + i * 4.62
    header(s, x, 2.46, 4.3, head_, size=T_CITE)
    yy = 2.78
    for item in items:
        b = textbox(s, x, yy, 4.38, 1.00)
        p = para(b.text_frame, item, size=16, color=BODY, line=1.20, space_after=0, first=True)
        bullet(p)
        yy += 1.02
cite(s, "Adjacent work, shaping what we feed it: Skill Self-Play arXiv:2607.22529 \u00b7 "
        "SkillWiki arXiv:2606.16523")
notes(s, """
兩個方向都是我們自己跑下來看到的問題，不是為了列論文而列。
上面那條成本數字就是動機：一次十幾個小時、燒掉的 token 不少，而最後被接受的修改
只有一兩次。效率還有很大空間。
SkillOpt-Lite 跟 NPO 是直接命中「同樣效果、更低訓練成本」這題的。
Skill Self-Play 跟 SkillWiki 是相鄰方向：一個講怎麼自動生出訓練任務，
一個講 domain knowledge 怎麼有來源地進到 skill 裡 —— 不要含糊地都說成「效率」。
第二個方向是我們自己的痛：SkillOpt 訓練出來的 skill 普遍偏長，而新的 domain knowledge
會一直加進來，只會越來越長。SkillZip 的角度很有意思 —— 免評估，靠找出可重用的結構來壓。
為什麼「免評估」對我們特別有價值：我們最貴的東西就是 rollout。
""")

# ═══════════════════════════════════════════════════ 26 · conclusions
s = blank(prs, bg=NAVY)
t = textbox(s, MARGIN, 0.58, CW, 0.40)
para(t.text_frame, "CONCLUSIONS", size=T_BODY, color=LIGHT, bold=True, space_after=0, first=True)
rule(s, y=1.02, w=2.5, h=0.04, color=ACCENT)
y = 1.42
for i, line in enumerate([
        "Every break in the loop is worth repairing",
        "A skill can be trained like a weight, with no model change",
        "The optimizer does not replace people; people run the last mile"], 1):
    n = textbox(s, MARGIN, y, 0.5, 0.40)
    para(n.text_frame, str(i), size=21, color=ACCENT, bold=True, space_after=0, first=True)
    b = textbox(s, 1.02, y, 8.4, 0.80)
    para(b.text_frame, line, size=21, color=WHITE, space_after=0, line=1.15, first=True)
    y += 0.92
t = textbox(s, MARGIN, 4.42, CW, 0.70)
tf = t.text_frame
para(tf, "SEED=1 ./scripts/dev.sh brings the whole thing up \u2014 all seven seams are fake by default",
     size=14, color=LIGHT, space_after=4, first=True)
para(tf, "Design record: backend/docs/spec.md  \u00b7  Routing: backend/docs/routing-optimization.md",
     size=14, color=LIGHT, space_after=0)
notes(s, """
這一頁是最後一張主投影片，Q&A 全程留在螢幕上，不要再翻到空白頁或「謝謝聆聽」。
三句話唸完就開放提問。
如果有人想自己試：repo 裡一行就起得來，七個 seam 預設全 fake，
不需要接任何真實服務就能把整個流程走一遍。
最可能被問到的三題，對應的附錄：接口怎麼接（附錄 A）、每個機制是不是都有用（附錄 B）、
有什麼是刻意沒做的（附錄 C）。
""")

# ═════════════════════════════════════════════════════ 27 · references
s = content("References")
refs = [
    "SkillOpt. arXiv:2605.23904, Microsoft. \u2014 Figures 1\u20134 and Tables 1, 3, 4 reproduced in this deck.",
    "microsoft/SkillOpt \u2014 the implementation vendored into Optimize.",
    "SkillOpt-Lite: Better and Faster Agent Self-evolution via One Line of Vibe. arXiv:2607.03451.",
    "Naive Prompt Optimization: Rethinking the Need for Complex Prompt Search. arXiv:2608.27266.",
    "Skill Self-Play: Pushing the Frontier of LLM Capability with Co-Evolving Skills. arXiv:2607.22529.",
    "SkillWiki: A Living Knowledge Infrastructure for Agent Skills. arXiv:2606.16523.",
    "SkillZip: Evaluation-Free Skill Compression for Self-Evolving Agents by Discovering Reusable Structure. arXiv:2608.11079.",
    "Langfuse \u2014 langfuse.com. Trace and span storage; read through its public API.",
    "Internal: backend/docs/spec.md \u00b7 routing-optimization.md \u00b7 agent-server-api.md",
]
box = textbox(s, MARGIN, 1.50, 9.0, 3.56)
for i, r in enumerate(refs):
    p = para(box.text_frame, r, size=T_CITE, color=BODY, space_after=8, line=1.15, first=(i == 0))
    bullet(p, char="", marL=0.24, hang=0.24)
notes(s, """
不用唸，留給想抄連結的人。被問到出處時翻回這一頁。
""")

# ═══════════════════════════════════════════════════ A1 · agent contract
s = appendix("Appendix A", "Your agent server implements two endpoints")
panel(s, MARGIN, 1.52, 9.0, 2.10, fill=PALE, line_color=RULE)
code = textbox(s, 0.72, 1.66, 8.56, 1.84)
tf = code.text_frame
for i, line in enumerate([
        'POST {chat endpoint}      an ordinary OpenAI chat completions call',
        '  "skill_studio": {',
        '    "trace_data": { "trace_id": "\u2026" }   \u2190 use this as the Langfuse trace id  (required)',
        '    "timeout_s": 120                   \u2190 this call\'s budget',
        '    "skills": { "billing/SKILL.md": "\u2026" }  \u2190 whole file set, this call only',
        '  }',
        'GET  {skills endpoint}    \u2192 {"skills": {path: text}, "version": "\u2026"}   (optional)']):
    para(tf, line, size=12, color=BODY, font=MONO, space_after=2, line=1.12, first=(i == 0))
bullets(s, MARGIN, 3.78, 9.0, 1.10, [
    "Without the correlation id the platform cannot find the trace it just caused",
    "skills replaces the directory rather than patching it, so {} and absent differ",
], size=17)
notes(s, """
只有在被問「我要怎麼把我的 agent 接上來」的時候才翻到這頁。
correlation id 是整個錯誤定位的前提，也是唯一一件必須在我們 repo 之外改的事。
skills 是替換不是 patch —— 只有替換才能表達「刪掉一個檔案」，所以空物件跟沒給
是兩種不同的請求：空物件的意思是「完全不帶 skill 回答這題」。
完整契約在 backend/docs/agent-server-api.md，app 裡的 Documentation 也讀得到。
""")

# ═══════════════════════════════════════════════════════ A2 · ablation
s = appendix("Appendix B", "Every mechanism pays for itself")
picture(s, str(FIG / "table3.png"), MARGIN, 1.50, 5.60, 3.10)
bullets(s, 6.35, 1.90, 3.15, 2.30, [
    "Each row removes one component and re-measures",
    "The learning-rate form, the rejected buffer and the epoch-wise update all earn their place",
], size=16)
cite(s, "Table 3. " + SRC)
notes(s, """
如果有人問「這些機制是不是每一個都真的有用」，翻這頁。
論文自己做了 component ablation：拿掉 learning-rate 的形式、拿掉 rejected buffer、
拿掉 epoch 層級的 slow / meta update，各自掉多少分。
""")

# ════════════════════════════════════════════════════════ A3 · limits
s = appendix("Appendix C", "What we deliberately did not build")
bullets(s, MARGIN, 1.52, 9.0, 3.30, [
    "Skills are never written back \u2014 the output is a zip",
    "Answer hard-coding has three defences",
    "Slow update and meta skill ship disabled",
    "Diagnosis accuracy itself is still unmeasured",
    "A tool or base-model error cannot be fixed by a skill",
], size=18)
cite(s, "Full list with reasoning: backend/docs/spec.md \u00a715.1 and \u00a716")
notes(s, """
這頁是可信度的來源，被問到的時候主動翻出來，不要等人挖。
寫回 agent server 需要 skill 更新 API 加版本控制 / rollback，那是另一個題目。
答案硬編的三層防線：analyst prompt 明文禁止、held-out 驗證切分是結構性防線、
再加上 diff 上的逐字比對告警。
診斷準確度沒被量化驗證這件事要講 —— 它正是決定要不要投入下一階段的判斷依據。
最後一條最重要：整個系統壓在「錯誤可歸因到單一 span，而且改 skill 修得好」這個假設上，
而這個假設有明確的裂縫。caveat 欄位就是為此存在的。
""")

prs.save(HERE / "Skill-Studio.pptx")
print("slides:", len(prs.slides._sldIdLst))

