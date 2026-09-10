// Builds the sprint-demo deck. Run with pptxgenjs available on NODE_PATH:
//   node presentation/build_deck.js [output.pptx]
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || "presentation/sprint-demo.pptx";

// Palette — slate + teal, chosen to read as "infrastructure", not marketing.
const INK = "1F2933";      // dark ground for the open/close slides
const INK_SOFT = "2F3B47"; // cards on the dark ground
const BODY = "3A4750";
const MUTED = "6B7784";
const MUTED_LT = "A8B4BE";
const PANEL = "F1F4F6";
const PANEL_2 = "E4EAEE";
const TEAL = "028090";
const TEAL_DK = "01606B";
const MINT = "02C39A";
const WHITE = "FFFFFF";
const CODE_BG = "F5F7F8";

const H = "Arial";
const B = "Calibri";
const M = "Courier New";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.author = "Sprint demo";
pres.title = "Plugging your domain agent into the platform";

const W = 13.333;
const ML = 0.7;              // left margin
const CW = W - ML * 2;       // content width

// ---------- helpers ----------
function titleSlide(s, text, eyebrow) {
  if (eyebrow) {
    s.addText(eyebrow.toUpperCase(), {
      x: ML, y: 0.42, w: CW, h: 0.3, isTextBox: true, margin: 0,
      fontFace: H, fontSize: 11, bold: true, color: TEAL, charSpacing: 2,
    });
  }
  s.addText(text, {
    x: ML, y: eyebrow ? 0.72 : 0.6, w: CW, h: 0.75, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 30, bold: true, color: INK,
  });
}

function card(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.08,
    fill: { color: o.fill || PANEL },
    line: { color: o.line || o.fill || PANEL, width: 1 },
  });
}

function codeBlock(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.06,
    fill: { color: CODE_BG }, line: { color: PANEL_2, width: 1 },
  });
  s.addText(o.text, {
    x: o.x + 0.16, y: o.y + 0.12, w: o.w - 0.32, h: o.h - 0.24,
    isTextBox: true, margin: 0, fontFace: M, fontSize: o.fontSize || 9.5,
    color: o.color || BODY, lineSpacing: o.lineSpacing || 13, valign: "top",
  });
}

// The deck's one repeated motif: a filled rounded token carrying a short label.
function token(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w || 0.42, h: o.h || 0.42, rectRadius: 0.1,
    fill: { color: o.fill || TEAL }, line: { color: o.fill || TEAL, width: 1 },
  });
  s.addText(o.label, {
    x: o.x, y: o.y, w: o.w || 0.42, h: o.h || 0.42, isTextBox: true, margin: 0,
    fontFace: H, fontSize: o.fontSize || 14, bold: true,
    color: o.color || WHITE, align: "center", valign: "middle",
  });
}

function bullets(s, items, o) {
  s.addText(
    items.map((t, i) => ({
      text: t, options: { bullet: true, breakLine: i !== items.length - 1 },
    })),
    {
      x: o.x, y: o.y, w: o.w, h: o.h, isTextBox: true, margin: 0,
      fontFace: B, fontSize: o.fontSize || 13.5, color: o.color || BODY,
      paraSpaceAfter: o.paraSpaceAfter || 7, valign: "top",
    }
  );
}

function footnote(s, text) {
  s.addText(text, {
    x: ML, y: 6.82, w: CW, h: 0.32, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 11, italic: true, color: MUTED,
  });
}

// ============================================================ 1 — title
let s = pres.addSlide();
s.background = { color: INK };
s.addText("SPRINT DEMO", {
  x: ML, y: 2.0, w: CW, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 12, bold: true, color: MINT, charSpacing: 3,
});
s.addText("Plugging your domain agent\ninto the platform", {
  x: ML, y: 2.4, w: 9.6, h: 1.9, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 40, bold: true, color: WHITE, lineSpacing: 46,
});
s.addText(
  "Two endpoints, three levels of integration — and what we learned pointing the eval loop at skill selection.",
  { x: ML, y: 4.45, w: 9.8, h: 0.5, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 15.5, color: MUTED_LT }
);
token(s, { x: ML, y: 5.35, w: 0.34, h: 0.34, label: "1", fontSize: 12 });
s.addText("The HTTP seam — how your agent connects", {
  x: ML + 0.52, y: 5.35, w: 6.5, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 13.5, color: WHITE, valign: "middle",
});
token(s, { x: ML, y: 5.85, w: 0.34, h: 0.34, label: "2", fontSize: 12, fill: INK_SOFT });
s.addText("Evaluating skill selection, and optimizing it", {
  x: ML + 0.52, y: 5.85, w: 6.5, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 13.5, color: MUTED_LT, valign: "middle",
});
s.addNotes(
  "開場（約30秒）。\n" +
  "這個 sprint 我大部分時間花在讓 agent server 的 HTTP 介面變得 general，" +
  "所以今天主要想講的是：你們各自的 domain agent 要怎麼接上這個系統。\n" +
  "整場大概 10 分鐘，Part 1 佔 8 分鐘，Part 2 兩頁快速帶過。"
);

// ============================================================ 2 — the seam
s = pres.addSlide();
titleSlide(s, "Where your agent meets the platform", "Part 1 · The seam");

// platform block
card(s, { x: ML, y: 1.75, w: 4.3, h: 3.75, fill: PANEL, line: PANEL_2 });
s.addText("THE PLATFORM", {
  x: ML + 0.28, y: 1.98, w: 3.8, h: 0.28, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 10.5, bold: true, color: TEAL, charSpacing: 2,
});
const feats = [
  ["Evaluation", "run an eval set, judge the answers, diagnose the failing span"],
  ["Playground", "edit a copy of the skill files, ask one question at a time"],
  ["Optimize", "train a skill against your eval set, epochs and all"],
];
feats.forEach(([name, desc], i) => {
  const y = 2.42 + i * 1.0;
  card(s, { x: ML + 0.28, y, w: 3.74, h: 0.85, fill: WHITE, line: PANEL_2 });
  s.addText(name, {
    x: ML + 0.46, y: y + 0.1, w: 3.4, h: 0.26, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 13, bold: true, color: INK,
  });
  s.addText(desc, {
    x: ML + 0.46, y: y + 0.36, w: 3.4, h: 0.42, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 10.5, color: MUTED,
  });
});

// http server block
card(s, { x: 5.45, y: 2.35, w: 2.5, h: 2.55, fill: TEAL, line: TEAL });
s.addText("YOUR HTTP SERVER", {
  x: 5.6, y: 2.58, w: 2.2, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 11, bold: true, color: WHITE, align: "center", charSpacing: 1,
});
s.addText("the only thing this\nsprint asks you to write", {
  x: 5.6, y: 2.9, w: 2.2, h: 0.5, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 10, italic: true, color: "BFE7E8", align: "center",
});
[["POST  chat", 3.5], ["GET  skills", 4.14]].forEach(([label, y]) => {
  s.addShape(pres.ShapeType.roundRect, {
    x: 5.65, y, w: 2.1, h: 0.5, rectRadius: 0.07,
    fill: { color: TEAL_DK }, line: { color: TEAL_DK, width: 1 },
  });
  s.addText(label, {
    x: 5.65, y, w: 2.1, h: 0.5, isTextBox: true, margin: 0,
    fontFace: M, fontSize: 11.5, bold: true, color: WHITE,
    align: "center", valign: "middle",
  });
});

// agent block
card(s, { x: 9.05, y: 2.35, w: 3.58, h: 2.55, fill: INK, line: INK });
s.addText("YOUR DOMAIN AGENT", {
  x: 9.25, y: 2.62, w: 3.2, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 11, bold: true, color: MINT, align: "center", charSpacing: 1,
});
s.addText("A black box.", {
  x: 9.25, y: 3.1, w: 3.2, h: 0.4, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 18, bold: true, color: WHITE, align: "center",
});
s.addText("The platform never sees your model,\nyour tools, or your prompt — only\nwhat these two endpoints return.", {
  x: 9.25, y: 3.6, w: 3.2, h: 0.9, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 11, color: MUTED_LT, align: "center",
});

// arrows
[[5.02, 5.42], [7.99, 9.02]].forEach(([x1, x2]) => {
  s.addShape(pres.ShapeType.line, {
    x: x1, y: 3.62, w: x2 - x1, h: 0,
    line: { color: MUTED, width: 1.75, beginArrowType: "triangle", endArrowType: "triangle" },
  });
});
s.addText("HTTP", {
  x: 4.95, y: 3.24, w: 0.6, h: 0.3, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 9.5, color: MUTED, align: "center",
});
s.addText("your call", {
  x: 8.0, y: 3.24, w: 1.0, h: 0.3, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 9.5, color: MUTED, align: "center",
});

s.addText(
  "Traces close the loop: your agent writes to Langfuse under the trace id we send, and the platform reads them back.",
  { x: ML, y: 5.75, w: CW, h: 0.34, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 12.5, color: BODY }
);
footnote(s, "Everything left of the seam is ours to maintain. Everything right of it stays yours.");
s.addNotes(
  "這頁大概 1 分鐘。\n" +
  "先建立整體圖像：左邊是這個系統的三個功能，右邊是各位自己的 domain agent，" +
  "系統完全不碰你的 model、tool、prompt，它是個 black box。\n" +
  "中間唯一需要你寫的，就是一個 HTTP server，上面兩個 endpoint。\n" +
  "另外提一下 trace：agent 要用我們給的 trace_id 寫 Langfuse，這是後面 optimization 的前提，等一下會再講。"
);

// ============================================================ 3 — two endpoints
s = pres.addSlide();
titleSlide(s, "Two endpoints — and only the first is required", "Part 1 · The contract");

const epCards = [
  {
    x: ML, sig: "POST   {chat endpoint}", purpose: "Answer one question.",
    fill: TEAL, sigColor: WHITE, textColor: WHITE, descColor: "CFE9EB",
    items: [
      "A plain OpenAI chat completions endpoint — if you already have one, you are already integrated",
      "Single-shot: no conversation, no session, no state between calls",
      "Everything the platform needs beyond the standard rides in one extra top-level key: skill_studio",
      "Ignore any key you do not recognise",
    ],
  },
  {
    x: ML + 6.16, sig: "GET   {skills endpoint}", purpose: "List your skill files.",
    fill: PANEL, sigColor: INK, textColor: BODY, descColor: MUTED, line: PANEL_2,
    items: [
      "Optional — without it, evaluation still runs normally",
      "Returns your whole skill workspace as a flat {path: text} map",
      "Plus a version string that moves whenever your answers would move",
      "No parameters, no request body",
    ],
  },
];
epCards.forEach((c) => {
  card(s, { x: c.x, y: 1.75, w: 5.78, h: 3.5, fill: c.fill, line: c.line || c.fill });
  s.addText(c.sig, {
    x: c.x + 0.32, y: 2.0, w: 5.14, h: 0.36, isTextBox: true, margin: 0,
    fontFace: M, fontSize: 15, bold: true, color: c.sigColor,
  });
  s.addText(c.purpose, {
    x: c.x + 0.32, y: 2.42, w: 5.14, h: 0.32, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 15, bold: true, color: c.sigColor,
  });
  bullets(s, c.items, {
    x: c.x + 0.32, y: 2.95, w: 5.14, h: 2.4, color: c.textColor, fontSize: 12.5,
    paraSpaceAfter: 9,
  });
});

card(s, { x: ML, y: 5.45, w: CW, h: 1.1, fill: WHITE, line: PANEL_2 });
s.addText("Authentication unlocks nothing", {
  x: ML + 0.3, y: 5.6, w: CW - 0.6, h: 0.28, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 13, bold: true, color: INK,
});
s.addText("A server that demands a credential and one that demands none are equally usable here. If yours sits behind a gateway, a developer types the key beside the URL and every request carries it — as a Bearer header, or as a header you name. With no key entered, the request is byte for byte what it was before.", {
  x: ML + 0.3, y: 5.92, w: CW - 0.6, h: 0.5, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 11.5, color: BODY,
});
footnote(s, "Two absolute URLs, entered separately. Nothing is appended to a base URL and no path is imposed — different prefixes, gateways, even different hosts are all fine.");
s.addNotes(
  "約 1 分鐘。\n" +
  "重點一句話：只有 chat endpoint 是必須的，而且它就是一個標準的 OpenAI compatible chat completions endpoint。" +
  "大部分 agent 前面本來就有一個，所以你今天其實就可以被 evaluate。\n" +
  "skills endpoint 是選配的，它讓系統知道你身上現在有哪些 skill 檔案。\n" +
  "另外強調：這是兩個各自獨立的絕對 URL，我們不會幫你組路徑，你要放在不同 host、不同 gateway 後面都可以。"
);

// ============================================================ 4 — chat endpoint
s = pres.addSlide();
titleSlide(s, "Chat endpoint — the request", "Part 1 · Schema");

codeBlock(s, {
  x: ML, y: 1.72, w: 6.5, h: 4.05, fontSize: 10, lineSpacing: 14,
  text:
'{\n' +
'  "model": "default",\n' +
'  "messages": [\n' +
'    {"role": "user", "content": "What was ACME\'s\n' +
'      outstanding balance at the end of Q2?"}\n' +
'  ],\n' +
'  "stream": false,\n' +
'  "skill_studio": {\n' +
'    "timeout_s": 115.0,\n' +
'    "trace_data": {\n' +
'      "trace_id":   "9f3e11c8a2b04d7e8c1f5a6b7d8e9f01",\n' +
'      "session_id": "9f3e11c8a2b04d7e8c1f5a6b7d8e9f01",\n' +
'      "user_id": "alice",\n' +
'      "tags": ["eval_billing"]\n' +
'    },\n' +
'    "skills": {                        // optional\n' +
'      "billing/SKILL.md": "---\\nname: billing\\n---# ..."\n' +
'    }\n' +
'  }\n' +
'}',
});

s.addTable(
  [
    [
      { text: "Field", options: { bold: true, color: WHITE, fill: { color: INK } } },
      { text: "What you do with it", options: { bold: true, color: WHITE, fill: { color: INK } } },
    ],
    ["messages", "Exactly one user message. No system message — your prompt stays yours."],
    ["model / stream", 'Constants ("default", false). You may ignore both.'],
    ["timeout_s", "Your budget for this call. Honour it, clamp it to a ceiling of your own, and return 504 on expiry."],
    ["trace_id", "Use the one we send as your Langfuse trace id. Mint your own and every trace-reading feature goes dark."],
    ["skills", "Absent → your own files. A map → these files only, for this call. {} → no skill files at all."],
  ],
  {
    x: ML + 6.75, y: 1.72, w: 5.18, colW: [1.35, 3.83],
    fontFace: B, fontSize: 10.5, color: BODY, valign: "top",
    border: { type: "solid", pt: 1, color: PANEL_2 },
    fill: { color: WHITE }, margin: 6, autoPage: false,
  }
);

card(s, { x: ML, y: 5.95, w: CW, h: 0.72, fill: PANEL, line: PANEL_2 });
s.addText(
  [
    { text: "Response — ", options: { bold: true, color: INK } },
    { text: "an ordinary chat completion; the answer is read from ", options: {} },
    { text: "choices[0].message.content", options: { fontFace: M, color: TEAL_DK } },
    { text: ". Never return an empty answer or a tool-call-only message — that fails the question rather than scoring it. If you have nothing to say, return a 5xx with a reason.", options: {} },
  ],
  { x: ML + 0.28, y: 6.05, w: CW - 0.56, h: 0.55, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 12, color: BODY }
);
s.addNotes(
  "約 2 分鐘，這是 Part 1 的核心頁之一。\n" +
  "整個 request 就是標準 OpenAI 格式，唯一的差別是多一個 top-level key: skill_studio。" +
  "為什麼不用 metadata？因為 OpenAI 的 metadata 限制 16 組短字串，skill 檔案塞不進去，嚴格的 gateway 還會直接擋掉。\n" +
  "三個欄位要特別講：\n" +
  "1) timeout_s：這是我們給你的預算，請照著做，但自己還是要有上限，不然一個 hang 住的 request 會佔住 worker。\n" +
  "2) trace_id：這個最 load-bearing。trace id 是我們先產生再送給你的，你要拿它去寫 Langfuse；" +
  "如果你自己產生，chat endpoint 看起來一切正常，但 step-by-step、失敗診斷、optimizer 全部會靜悄悄地失效。\n" +
  "3) skills：這是 override，等一下第三個 level 會再提。注意它有三種狀態，空的 {} 是「這次不要用任何 skill」，不是「用你自己的」。"
);

// ============================================================ 5 — skills endpoint
s = pres.addSlide();
titleSlide(s, "Skills endpoint — the response", "Part 1 · Schema");

codeBlock(s, {
  x: ML, y: 1.72, w: 6.5, h: 2.5, fontSize: 10, lineSpacing: 14,
  text:
'{\n' +
'  "version": "a1b2c3d",\n' +
'  "skills": {\n' +
'    "billing/SKILL.md":\n' +
'      "---\\nname: billing\\ndescription: Invoices,\n' +
'       balances and refunds.\\n---\\n# Billing ...",\n' +
'    "billing/references/refunds.md":\n' +
'      "# Refund rules\\nProrated by service days.\\n",\n' +
'    "reporting/SKILL.md": "# Reporting ..."\n' +
'  }\n' +
'}',
});

s.addText("Four rules that matter", {
  x: ML + 6.75, y: 1.72, w: 5.18, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 14, bold: true, color: INK,
});
bullets(s, [
  "Flat, not nested. One key per file, and walk every level — a skill is a directory, and its reference files matter as much as SKILL.md.",
  "Full text, never truncated. A developer edits this content and sends it back; a truncated file becomes a destructive edit.",
  "{} is a valid answer — an agent with no skills is a supported configuration. If you cannot read your own directory, return 5xx, never an empty map.",
  "version moves whenever your answers would move: a skill edit, a model swap, a prompt change, a redeploy. A constant is worse than nothing.",
], { x: ML + 6.75, y: 2.12, w: 5.18, h: 2.5, fontSize: 12, paraSpaceAfter: 8 });

s.addText("The skills override has three states — test for the key, not for truthiness", {
  x: ML, y: 4.72, w: CW, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 14, bold: true, color: INK,
});
const states = [
  ["key absent", "Use your own files, exactly as normal."],
  ["a populated map", "Use these files and only these, for this one call. Replacement, not a patch — and never persisted."],
  ["{}", "Use no skill files at all. “Does it still work without the skill?” is a question people deliberately ask."],
];
states.forEach(([k, v], i) => {
  const x = ML + i * 4.04;
  card(s, { x, y: 5.12, w: 3.84, h: 1.42, fill: WHITE, line: PANEL_2 });
  s.addText(k, {
    x: x + 0.22, y: 5.26, w: 3.4, h: 0.28, isTextBox: true, margin: 0,
    fontFace: M, fontSize: 12.5, bold: true, color: TEAL_DK,
  });
  s.addText(v, {
    x: x + 0.22, y: 5.58, w: 3.4, h: 0.85, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 11.5, color: BODY,
  });
});
footnote(s, "Path keys are attacker-influenced strings: reject `..`, a leading `/`, backslashes, NUL bytes and empty keys with a 400 before they become filesystem paths.");
s.addNotes(
  "約 1.5 分鐘。\n" +
  "skills endpoint 本身很單純：回一個 flat 的 {路徑: 全文} map，加一個 version。\n" +
  "四個雷區：不要做成樹狀、不要截斷檔案內容（開發者會編輯後送回來，截斷等於幫他刪檔）、" +
  "空的 {} 跟壞掉要分得開、version 不要回固定值（回固定值比不回還糟，因為兩個檢查都失效但看起來有在動）。\n" +
  "下半頁是 override 的三種狀態，這個是 optimization 整個功能的基礎。" +
  "特別提醒：程式要判斷 key 在不在，不要用 if (skills)，因為 {} 是 falsy，會把「不要用任何 skill」默默變成「用你自己的」。\n" +
  "路徑安全那行如果時間不夠可以跳過，文件裡有。"
);

// ============================================================ 6 — the ladder
s = pres.addSlide();
titleSlide(s, "You do not have to build all of it", "Part 1 · Three levels");
s.addText("The platform works with what you give it, and tells you what you are missing rather than refusing to start.", {
  x: ML, y: 1.42, w: CW, h: 0.3, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 13, color: MUTED,
});

const levels = [
  {
    n: "1", y: 3.35, h: 2.9, fill: PANEL, line: PANEL_2, head: INK, body: BODY, tok: TEAL,
    build: "An OpenAI-compatible\nchat endpoint",
    sub: "What most agents already have",
    unlock: "Evaluation",
    detail: "Run an eval set and see the score, the answers and the judge's verdicts — plus the slice of the playground that only needs an answer.",
  },
  {
    n: "2", y: 2.7, h: 3.55, fill: "D9EDEE", line: "BFE0E2", head: INK, body: BODY, tok: TEAL,
    build: "+ the skills endpoint\n+ honour the skills override",
    sub: "Two behaviours, one new route",
    unlock: "The full playground",
    detail: "View and edit a copy of your skill files, ask one question at a time against the edit, plus skill-coverage warnings and staleness detection when your server moves underneath you.",
  },
  {
    n: "3", y: 2.0, h: 4.25, fill: TEAL, line: TEAL, head: WHITE, body: "DDF0F1", tok: TEAL_DK,
    build: "+ traces in Langfuse under\nthe trace id we send",
    sub: "Not a third endpoint",
    unlock: "Optimization",
    detail: "Hundreds of rollouts against candidate versions of a skill, scored and compared, with a validation gate that discards the edits that did not help. The platform verifies both behaviours before a run starts.",
  },
];
levels.forEach((L, i) => {
  const x = ML + i * 4.04;
  card(s, { x, y: L.y, w: 3.84, h: L.h, fill: L.fill, line: L.line });
  token(s, { x: x + 0.24, y: L.y + 0.24, w: 0.44, h: 0.44, label: L.n, fill: L.tok, fontSize: 15 });
  s.addText(L.build, {
    x: x + 0.82, y: L.y + 0.2, w: 2.8, h: 0.6, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 12.5, bold: true, color: L.head,
  });
  s.addText(L.sub, {
    x: x + 0.24, y: L.y + 0.86, w: 3.36, h: 0.26, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 10.5, italic: true, color: i === 2 ? "BFE7E8" : MUTED,
  });
  s.addText("YOU GET", {
    x: x + 0.24, y: L.y + 1.22, w: 3.36, h: 0.24, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 9.5, bold: true, color: i === 2 ? MINT : TEAL, charSpacing: 2,
  });
  s.addText(L.unlock, {
    x: x + 0.24, y: L.y + 1.46, w: 3.36, h: 0.34, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 17, bold: true, color: L.head,
  });
  s.addText(L.detail, {
    x: x + 0.24, y: L.y + 1.94, w: 3.36, h: L.h - 2.14, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 11.5, color: L.body,
  });
});
footnote(s, "Every level is checked before it is offered: the platform names what is missing rather than failing halfway through a run.");
s.addNotes(
  "約 1.5 分鐘，這頁是 Part 1 想讓大家帶走的結論。\n" +
  "不要覺得這個 HTTP server 一定要做到很複雜，它可以分三個 level，每一級解鎖一塊功能：\n" +
  "Level 1：只有 OpenAI compatible 的 chat endpoint，就能用 evaluation，以及部分 playground。\n" +
  "Level 2：加上 skills endpoint，並且在 chat endpoint 支援 skill override，就能用完整的 playground。\n" +
  "Level 3：再加上把 trace 寫進 Langfuse（用我們給的 trace id），就能跑 optimization。\n" +
  "注意 level 3 不是第三個 endpoint，它是 chat endpoint 的兩個行為；系統在 optimization 開跑前會先驗這兩件事，" +
  "不然跑一小時只會得到一條平的曲線。\n" +
  "建議大家就從 level 1 開始，先被 evaluate 起來再說。"
);

// ============================================================ 7 — docs + test page (demo cue)
s = pres.addSlide();
titleSlide(s, "You probably should not write this by hand", "Part 1 · Getting there");

s.addText("The API reference is written to be handed to a machine: self-contained, no cross-references into our repo, with a reference implementation and an acceptance checklist at the end.", {
  x: ML, y: 1.5, w: 6.5, h: 0.7, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 13.5, color: BODY,
});

const steps = [
  ["Open the docs in the platform", "Agent Server → API reference. One page, 13 sections, everything the contract requires."],
  ["Point your coding assistant at it", "That page is the whole spec — it can implement your server against it without reading our codebase."],
  ["Test your server, in the platform", "The Test-your-server page sends a real question with a real skills override and tells you which level you have actually reached."],
];
steps.forEach(([head, body], i) => {
  const y = 2.42 + i * 1.24;
  token(s, { x: ML, y, w: 0.44, h: 0.44, label: String(i + 1), fontSize: 15 });
  s.addText(head, {
    x: ML + 0.66, y: y - 0.02, w: 5.8, h: 0.3, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 14.5, bold: true, color: INK,
  });
  s.addText(body, {
    x: ML + 0.66, y: y + 0.32, w: 5.8, h: 0.62, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 12, color: MUTED,
  });
});

// mock of the docs page in the product
card(s, { x: ML + 6.9, y: 1.72, w: 5.03, h: 4.3, fill: WHITE, line: PANEL_2 });
s.addShape(pres.ShapeType.roundRect, {
  x: ML + 6.9, y: 1.72, w: 5.03, h: 0.42, rectRadius: 0.06,
  fill: { color: PANEL }, line: { color: PANEL_2, width: 1 },
});
["E06C5E", "E0B65E", "7FBF6A"].forEach((c, i) => {
  s.addShape(pres.ShapeType.ellipse, {
    x: ML + 7.06 + i * 0.22, y: 1.87, w: 0.12, h: 0.12,
    fill: { color: c }, line: { color: c, width: 1 },
  });
});
s.addText("Documentation", {
  x: ML + 7.8, y: 1.76, w: 2.4, h: 0.32, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 10.5, color: MUTED, valign: "middle",
});
card(s, { x: ML + 6.9, y: 2.14, w: 1.85, h: 3.88, fill: "FAFBFC", line: PANEL_2 });
s.addText("AGENT SERVER", {
  x: ML + 7.06, y: 2.32, w: 1.6, h: 0.24, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 8.5, bold: true, color: MUTED, charSpacing: 1,
});
s.addShape(pres.ShapeType.roundRect, {
  x: ML + 6.98, y: 2.62, w: 1.7, h: 0.34, rectRadius: 0.05,
  fill: { color: "D9EDEE" }, line: { color: "D9EDEE", width: 1 },
});
s.addText("API reference", {
  x: ML + 7.08, y: 2.62, w: 1.6, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 10, bold: true, color: TEAL_DK, valign: "middle",
});
s.addText("Test your server", {
  x: ML + 7.08, y: 3.0, w: 1.6, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 10, color: BODY, valign: "middle",
});
s.addText("Agent Server API", {
  x: ML + 8.95, y: 2.36, w: 2.8, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 13, bold: true, color: INK,
});
[
  "1  What each endpoint unlocks",
  "3  Chat endpoint",
  "4  Skills endpoint",
  "5  The skills override, in detail",
  "9  Errors, and what each causes",
  "11  Acceptance checklist",
  "12  A minimal reference impl.",
].forEach((line, i) => {
  s.addText(line, {
    x: ML + 8.95, y: 2.78 + i * 0.32, w: 2.85, h: 0.28, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 10, color: MUTED, valign: "middle",
  });
});

s.addText("→  DEMO: the docs page, then Test your server", {
  x: ML, y: 6.25, w: CW, h: 0.42, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 15, bold: true, color: TEAL,
});
s.addNotes(
  "約 1.5 分鐘，講完就切到系統上 demo。\n" +
  "這頁的重點：不要自己從零看規格寫。文件是刻意寫成自給自足的——" +
  "不需要讀我們的 codebase，最後還附了一個最小的 reference implementation 和一份 acceptance checklist（curl 可以直接跑）。\n" +
  "所以最快的路徑是：把這一頁餵給你的 coding assistant，讓它幫你把 server 實作出來。\n" +
  "然後切到系統：先秀 Agent Server → API reference 這頁，捲一下目錄讓大家看到涵蓋範圍；" +
  "再切到旁邊的 Test your server，說明它會送一個帶 skill override 的真實問題過去，" +
  "回來告訴你你實際上做到第幾級。\n" +
  "最後補一句：如果文件有讓你需要猜的地方，那是文件的 bug，直接跟我說。"
);

// ============================================================ 8 — skill selection eval
s = pres.addSlide();
titleSlide(s, "The same eval loop, pointed at skill selection", "Part 2 · A second use");
s.addText("Evaluation was built to grade the answer. It grades a routing decision just as well — the trick is entirely in how the question is phrased.", {
  x: ML, y: 1.45, w: CW, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 13.5, color: MUTED,
});

const qs = [
  {
    x: ML, tag: "ANSWER EVALUATION", fill: PANEL, line: PANEL_2, tagColor: MUTED,
    q: "What will TSMC's capacity be next year?",
    graded: "Graded on: the answer the agent produced.",
  },
  {
    x: ML + 6.16, tag: "ROUTING EVALUATION", fill: "D9EDEE", line: "BFE0E2", tagColor: TEAL_DK,
    q: "Which skill would you pick to answer the following question: What will TSMC's capacity be next year?",
    graded: "Graded on: the skill the agent names.",
  },
];
qs.forEach((c) => {
  card(s, { x: c.x, y: 2.0, w: 5.78, h: 2.35, fill: c.fill, line: c.line });
  s.addText(c.tag, {
    x: c.x + 0.3, y: 2.22, w: 5.2, h: 0.26, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 9.5, bold: true, color: c.tagColor, charSpacing: 2,
  });
  s.addText("“" + c.q + "”", {
    x: c.x + 0.3, y: 2.56, w: 5.2, h: 1.05, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 15, bold: true, color: INK,
  });
  s.addText(c.graded, {
    x: c.x + 0.3, y: 3.78, w: 5.2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 12, italic: true, color: BODY,
  });
});
s.addShape(pres.ShapeType.line, {
  x: ML + 5.86, y: 3.17, w: 0.26, h: 0,
  line: { color: TEAL, width: 2, endArrowType: "triangle" },
});

s.addText("What that buys us", {
  x: ML, y: 4.62, w: CW, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 14, bold: true, color: INK,
});
const buys = [
  ["No new feature", "Same upload, same run, same judge, same failure diagnosis. Routing became measurable without a line of new evaluation code."],
  ["The label was already there", "Every eval question already carries a skill field — the skills it should have been answered with. That is the ground truth a routing question needs."],
  ["A cheaper signal", "The agent stops before it works. One decision per question instead of a full trajectory, so a routing set runs fast and costs little."],
];
buys.forEach(([head, body], i) => {
  const x = ML + i * 4.04;
  card(s, { x, y: 5.02, w: 3.84, h: 1.55, fill: WHITE, line: PANEL_2 });
  s.addText(head, {
    x: x + 0.24, y: 5.18, w: 3.4, h: 0.28, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 12.5, bold: true, color: TEAL_DK,
  });
  s.addText(body, {
    x: x + 0.24, y: 5.5, w: 3.4, h: 0.95, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 11, color: BODY,
  });
});
s.addNotes(
  "約 1 分鐘。\n" +
  "這是 Part 2 的第一件事：這個系統除了評 agent 的回答，我們也拿它來評 agent「選 skill」的能力。\n" +
  "做法很土但有效：把 evaluation question 從「台積電明年產能是多少」改寫成" +
  "「請回答下面這個問題你會選擇哪一個 skill：台積電明年產能是多少」。\n" +
  "這樣現有的 evaluation 功能完全不用改就能用——一樣的上傳格式、一樣的 run、一樣的 judge。\n" +
  "而且 ground truth 本來就在：eval set 的每一題本來就有 skill 這個欄位。"
);

// ============================================================ 9 — routing optimization
s = pres.addSlide();
titleSlide(s, "Routing optimization: why SkillOpt did not transfer", "Part 2 · In flight");

card(s, { x: ML, y: 1.72, w: 6.06, h: 3.05, fill: PANEL, line: PANEL_2 });
s.addText("WHAT WE TRIED", {
  x: ML + 0.3, y: 1.94, w: 5.4, h: 0.26, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 9.5, bold: true, color: MUTED, charSpacing: 2,
});
s.addText("Isolated mode's algorithm, pointed at the description", {
  x: ML + 0.3, y: 2.24, w: 5.46, h: 0.34, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 15, bold: true, color: INK,
});
s.addText("Small minibatches, an analyst call each, then a merge. It does not converge: the description keeps being rewritten and the score keeps moving without settling.", {
  x: ML + 0.3, y: 2.66, w: 5.46, h: 0.7, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 12, color: BODY,
});
s.addText("Optimizing a skill body is a large model: thousands of words, an edit appends to one section, and the loss follows the gradient down. A description is two or three sentences — a representation space so small that every edit is a rewrite, so the loss jumps across the surface instead of descending it.", {
  x: ML + 0.3, y: 3.42, w: 5.46, h: 1.2, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 12, italic: true, color: TEAL_DK,
});

card(s, { x: ML + 6.32, y: 1.72, w: 5.61, h: 3.05, fill: TEAL, line: TEAL });
s.addText("WHAT WE DO NOW", {
  x: ML + 6.62, y: 1.94, w: 5.0, h: 0.26, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 9.5, bold: true, color: MINT, charSpacing: 2,
});
s.addText("One larger, stratified batch per step", {
  x: ML + 6.62, y: 2.24, w: 5.0, h: 0.34, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 15, bold: true, color: WHITE,
});
bullets(s, [
  "A big batch gives a more global gradient than a handful of questions can.",
  "Stratified sampling interleaves the skills, so every description is rewritten from evidence about itself — not dragged by whichever skill happened to turn up.",
  "One analyst call over the whole batch: nothing left for a merge stage to pick between with the evidence already discarded.",
], { x: ML + 6.62, y: 2.66, w: 5.0, h: 1.95, color: "E1F2F3", fontSize: 12, paraSpaceAfter: 7 });

card(s, { x: ML, y: 4.95, w: 6.06, h: 1.6, fill: WHITE, line: PANEL_2 });
s.addText("The context problem a big batch creates", {
  x: ML + 0.3, y: 5.12, w: 5.46, h: 0.28, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 13, bold: true, color: INK,
});
s.addText("Hundreds of full traces do not fit in the optimizer's context window. So the step sends a digest instead: a per-skill confusion matrix of what was tagged versus what was opened, plus the agent's own setup, folded once and marked where it varied.", {
  x: ML + 0.3, y: 5.44, w: 5.46, h: 1.0, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 11.5, color: BODY,
});

card(s, { x: ML + 6.32, y: 4.95, w: 5.61, h: 1.6, fill: "FFF4E0", line: "F0DFB8" });
token(s, { x: ML + 6.62, y: 5.14, w: 0.4, h: 0.4, label: "!", fill: "C98A12", fontSize: 15 });
s.addText("Still in flight", {
  x: ML + 7.14, y: 5.14, w: 4.6, h: 0.4, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 14, bold: true, color: "8A5E08", valign: "middle",
});
s.addText("The experiments are running now — no convergence numbers to show yet. What is settled is the diagnosis and the shape of the fix; the results land next sprint.", {
  x: ML + 6.62, y: 5.62, w: 5.0, h: 0.8, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 11.5, color: "6B4A06",
});
s.addNotes(
  "約 1 分鐘，快速帶過即可（時間不夠可以只講左半邊然後跳到最後一頁）。\n" +
  "我們原本想直接沿用 SkillOpt 的想法，也就是 isolation mode 那一套，只是把目標換成 skill description。結果發現行不通，description 很難收斂。\n" +
  "後來想清楚原因：optimize 一個 skill body，等於在調一個參數量很大的模型，每次改一點，loss 可以沿著 gradient 慢慢往下走。" +
  "但 description 常常只有兩三句話，representation space 很小，改一點就等於整個重寫，" +
  "loss 就在 loss surface 上大幅震盪，收不起來。\n" +
  "所以現在做法比較樸素：用更大的 batch 加上 stratified sampling，算一個比較 global、而且同時考慮到所有 skill 的 gradient，" +
  "避免被某一個 skill 帶偏。\n" +
  "但 batch 一大，optimizer 的 context window 就塞不下每題的 trace，所以我們改送一個 digest：" +
  "每個 skill 的 confusion matrix，加上 agent 自己的 setup。\n" +
  "這邊實驗還在跑，還沒有數字，下個 sprint 再跟大家報告。"
);

// ============================================================ 10 — closing
s = pres.addSlide();
s.background = { color: INK };
s.addText("WHAT I'D LIKE FROM YOU", {
  x: ML, y: 1.15, w: CW, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 12, bold: true, color: MINT, charSpacing: 3,
});
s.addText("Start at level 1. It is smaller than it looks.", {
  x: ML, y: 1.55, w: 10.5, h: 0.6, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 30, bold: true, color: WHITE,
});

const asks = [
  ["Bring the chat endpoint you already have", "If it speaks OpenAI chat completions, honour timeout_s and reuse our trace id, you can be evaluated this week."],
  ["Hand the API reference to your coding assistant", "It is written for exactly that: self-contained, with a reference implementation and a curl acceptance checklist."],
  ["Prove it with Test your server", "Before you wire up a run — it tells you which of the three levels you have actually reached."],
  ["Tell me where the doc made you guess", "Ambiguity in that page is a bug in that page, and it is the cheapest thing on this list for me to fix."],
];
asks.forEach(([head, body], i) => {
  const x = ML + (i % 2) * 6.16;
  const y = 2.6 + Math.floor(i / 2) * 1.75;
  card(s, { x, y, w: 5.78, h: 1.5, fill: INK_SOFT, line: INK_SOFT });
  token(s, { x: x + 0.28, y: y + 0.26, w: 0.4, h: 0.4, label: String(i + 1), fill: TEAL, fontSize: 14 });
  s.addText(head, {
    x: x + 0.8, y: y + 0.24, w: 4.75, h: 0.44, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 13.5, bold: true, color: WHITE,
  });
  s.addText(body, {
    x: x + 0.8, y: y + 0.74, w: 4.75, h: 0.68, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 11.5, color: MUTED_LT,
  });
});
s.addText("Questions?", {
  x: ML, y: 6.35, w: CW, h: 0.4, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 17, bold: true, color: MINT,
});
s.addNotes(
  "收尾，約 30 秒。\n" +
  "重申一次最重要的話：從 level 1 開始就好，門檻比大家想像的低——" +
  "如果你前面已經有一個 OpenAI compatible 的 endpoint，只要照著文件補上 timeout_s 跟沿用我們的 trace id，這週就可以被 evaluate。\n" +
  "文件是給機器讀的，丟給你的 coding assistant；接好之後用 Test your server 驗一下你到第幾級。\n" +
  "最後拜託一件事：文件裡任何讓你需要猜的地方都跟我說，那是文件的 bug，我修起來最便宜。"
);

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));
