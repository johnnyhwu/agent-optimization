// Builds the sprint-demo deck. Run with pptxgenjs available on NODE_PATH:
//   node presentation/build_deck.js [output.pptx]
//
// The slides carry diagrams and labels; the argument lives in the speaker
// notes, which are Chinese. If you are tempted to add a sentence to a slide,
// add it to addNotes instead.
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || "presentation/sprint-demo.pptx";

// Palette — slate + teal, chosen to read as "infrastructure", not marketing.
const INK = "1F2933";
const INK_SOFT = "2F3B47";
const BODY = "3A4750";
const MUTED = "6B7784";
const MUTED_LT = "A8B4BE";
const PANEL = "F1F4F6";
const PANEL_2 = "E4EAEE";
const TEAL = "028090";
const TEAL_DK = "01606B";
const TEAL_LT = "D9EDEE";
const TEAL_LN = "BFE0E2";
const MINT = "02C39A";
const AMBER = "C98A12";
const AMBER_BG = "FFF4E0";
const AMBER_LN = "F0DFB8";
const AMBER_TX = "8A5E08";
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
const ML = 0.7;
const CW = W - ML * 2;

// ---------- helpers ----------
function heading(s, text, eyebrow, sub) {
  if (eyebrow) {
    s.addText(eyebrow.toUpperCase(), {
      x: ML, y: 0.42, w: CW, h: 0.3, isTextBox: true, margin: 0, valign: "top",
      fontFace: H, fontSize: 11, bold: true, color: TEAL, charSpacing: 2,
    });
  }
  s.addText(text, {
    x: ML, y: eyebrow ? 0.72 : 0.6, w: CW, h: 0.6, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 30, bold: true, color: INK,
  });
  if (sub) {
    s.addText(sub, {
      x: ML, y: 1.44, w: CW, h: 0.3, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 13, color: MUTED,
    });
  }
}

function card(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: o.radius || 0.08,
    fill: { color: o.fill || PANEL },
    line: { color: o.line || o.fill || PANEL, width: 1, dashType: o.dash },
  });
}

function codeBlock(s, o) {
  card(s, { x: o.x, y: o.y, w: o.w, h: o.h, fill: CODE_BG, line: PANEL_2, radius: 0.06 });
  s.addText(o.text, {
    x: o.x + 0.18, y: o.y + 0.14, w: o.w - 0.36, h: o.h - 0.28,
    isTextBox: true, margin: 0, fontFace: M, fontSize: o.fontSize || 10,
    color: o.color || BODY, lineSpacing: o.lineSpacing || 14, valign: "top",
  });
}

// The deck's repeated motif: a filled rounded token carrying a short label.
function token(s, o) {
  const w = o.w || 0.42;
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w, h: o.h || 0.42, rectRadius: 0.1,
    fill: { color: o.fill || TEAL }, line: { color: o.fill || TEAL, width: 1 },
  });
  s.addText(o.label, {
    x: o.x, y: o.y, w, h: o.h || 0.42, isTextBox: true, margin: 0,
    fontFace: H, fontSize: o.fontSize || 14, bold: true,
    color: o.color || WHITE, align: "center", valign: "middle",
  });
}

// A labelled box: the diagram unit everything else is built from.
function chip(s, o) {
  card(s, {
    x: o.x, y: o.y, w: o.w, h: o.h || 0.4,
    fill: o.fill || WHITE, line: o.line || PANEL_2, radius: 0.06, dash: o.dash,
  });
  if (o.label === undefined) return;
  s.addText(o.label, {
    x: o.x + 0.1, y: o.y, w: o.w - 0.2, h: o.h || 0.4, isTextBox: true, margin: 0,
    fontFace: o.mono ? M : B, fontSize: o.fontSize || 11.5, bold: o.bold,
    color: o.color || BODY, align: o.align || "center", valign: "middle",
  });
}

function label(s, o) {
  s.addText(o.text, {
    x: o.x, y: o.y, w: o.w, h: o.h || 0.26, isTextBox: true, margin: 0, valign: "top",
    fontFace: o.face || H, fontSize: o.fontSize || 9.5, bold: o.bold !== false,
    color: o.color || MUTED, charSpacing: o.charSpacing === undefined ? 2 : o.charSpacing,
    align: o.align || "left", italic: o.italic,
  });
}

function arrowH(s, o) {
  s.addShape(pres.ShapeType.line, {
    x: o.x, y: o.y, w: o.w, h: 0,
    line: {
      color: o.color || MUTED, width: o.width || 1.75,
      endArrowType: o.end === false ? "none" : "triangle",
      beginArrowType: o.begin ? "triangle" : "none",
    },
  });
}

function arrowV(s, o) {
  s.addShape(pres.ShapeType.line, {
    x: o.x, y: o.y, w: 0, h: o.h,
    line: {
      color: o.color || MUTED, width: o.width || 1.75,
      endArrowType: o.end === false ? "none" : "triangle",
      beginArrowType: o.begin ? "triangle" : "none",
    },
  });
}

function footnote(s, text) {
  s.addText(text, {
    x: ML, y: 6.85, w: CW, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11, italic: true, color: MUTED,
  });
}

// ============================================================ 1 — title
let s = pres.addSlide();
s.background = { color: INK };
label(s, { x: ML, y: 2.0, w: CW, text: "SPRINT DEMO", fontSize: 12, color: MINT, charSpacing: 3 });
s.addText("Plugging your domain agent\ninto the platform", {
  x: ML, y: 2.4, w: 9.6, h: 1.9, isTextBox: true, margin: 0, valign: "top",
  fontFace: H, fontSize: 40, bold: true, color: WHITE, lineSpacing: 46,
});
s.addText("Two endpoints. Three levels. One black box.", {
  x: ML, y: 4.45, w: 9.8, h: 0.4, isTextBox: true, margin: 0, valign: "top",
  fontFace: B, fontSize: 16, color: MUTED_LT,
});
token(s, { x: ML, y: 5.35, w: 0.34, h: 0.34, label: "1", fontSize: 12 });
s.addText("The HTTP seam", {
  x: ML + 0.52, y: 5.35, w: 6.5, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 13.5, color: WHITE, valign: "middle",
});
token(s, { x: ML, y: 5.85, w: 0.34, h: 0.34, label: "2", fontSize: 12, fill: INK_SOFT });
s.addText("Evaluating skill selection", {
  x: ML + 0.52, y: 5.85, w: 6.5, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 13.5, color: MUTED_LT, valign: "middle",
});
s.addNotes(
  "開場（約 30 秒）。\n" +
  "這個 sprint 我大部分時間花在讓 agent server 的 HTTP 介面變得 general，" +
  "所以今天主要想講的是：你們各自的 domain agent 要怎麼接上這個系統。\n" +
  "整場大概 10 分鐘：Part 1 講怎麼接（8 分鐘），Part 2 兩頁快速帶過我們拿這個系統做的另一件事。"
);

// ============================================================ 2 — the seam
s = pres.addSlide();
heading(s, "Where your agent meets the platform", "Part 1 · The seam");

card(s, { x: ML, y: 1.8, w: 4.3, h: 3.3, fill: PANEL, line: PANEL_2 });
label(s, { x: ML + 0.28, y: 2.02, w: 3.8, text: "The platform", fontSize: 10.5, color: TEAL });
[
  ["Evaluation", "score an eval set"],
  ["Playground", "try one question"],
  ["Optimize", "train a skill"],
].forEach(([name, tag], i) => {
  const y = 2.4 + i * 0.82;
  card(s, { x: ML + 0.28, y, w: 3.74, h: 0.72, fill: WHITE, line: PANEL_2 });
  s.addText(name, {
    x: ML + 0.48, y: y + 0.08, w: 3.4, h: 0.26, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 13, bold: true, color: INK,
  });
  s.addText(tag, {
    x: ML + 0.48, y: y + 0.36, w: 3.4, h: 0.26, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11, color: MUTED,
  });
});

card(s, { x: 5.45, y: 2.35, w: 2.5, h: 2.25, fill: TEAL, line: TEAL });
label(s, { x: 5.55, y: 2.6, w: 2.3, text: "Your HTTP server", fontSize: 11, color: WHITE, align: "center", charSpacing: 1 });
chip(s, { x: 5.65, y: 3.0, w: 2.1, h: 0.5, label: "POST  chat", mono: true, bold: true, fill: TEAL_DK, line: TEAL_DK, color: WHITE, fontSize: 11.5 });
chip(s, { x: 5.65, y: 3.6, w: 2.1, h: 0.5, label: "GET  skills", mono: true, bold: true, fill: TEAL_DK, line: TEAL_DK, color: WHITE, fontSize: 11.5 });

card(s, { x: 9.05, y: 2.35, w: 3.58, h: 2.25, fill: INK, line: INK });
label(s, { x: 9.25, y: 2.6, w: 3.2, text: "Your domain agent", fontSize: 11, color: MINT, align: "center", charSpacing: 1 });
s.addText("A black box.", {
  x: 9.25, y: 3.0, w: 3.2, h: 0.42, isTextBox: true, margin: 0, valign: "top",
  fontFace: H, fontSize: 19, bold: true, color: WHITE, align: "center",
});
s.addText("your model, your tools, your prompt", {
  x: 9.25, y: 3.55, w: 3.2, h: 0.6, isTextBox: true, margin: 0, valign: "top",
  fontFace: B, fontSize: 11.5, color: MUTED_LT, align: "center",
});

arrowH(s, { x: 5.02, y: 3.47, w: 0.4, begin: true });
arrowH(s, { x: 7.99, y: 3.47, w: 1.03, begin: true });
label(s, { x: 4.85, y: 3.08, w: 0.75, text: "HTTP", fontSize: 9.5, align: "center", charSpacing: 1 });
label(s, { x: 8.0, y: 3.08, w: 1.0, text: "yours", fontSize: 9.5, align: "center", charSpacing: 1 });

// the return path: traces go out to Langfuse and are read back by the platform
arrowV(s, { x: 10.84, y: 4.6, h: 0.9, end: false, width: 1.5 });
arrowH(s, { x: 2.85, y: 5.5, w: 7.99, begin: true, end: false, width: 1.5 });
arrowV(s, { x: 2.85, y: 5.1, h: 0.4, begin: true, end: false, width: 1.5 });
chip(s, { x: 5.15, y: 5.28, w: 3.4, h: 0.44, label: "traces, via Langfuse", fill: WHITE, line: PANEL_2, color: MUTED });

footnote(s, "Left of the seam is ours. Right of it is yours. The seam itself is what this sprint made general.");
s.addNotes(
  "約 1 分鐘。\n" +
  "先建立整體圖像。左邊是這個系統的三塊功能：evaluation 跑整份 eval set 打分數並診斷失敗的 span；" +
  "playground 讓你改一份 skill 的副本、單獨問一題；optimize 則是拿你的 eval set 去訓練一個 skill。\n" +
  "右邊是各位自己的 domain agent，對這個系統來說它是一個 black box——" +
  "我們完全不知道也不在乎你裡面用什麼 model、什麼 tool、什麼 prompt。\n" +
  "中間唯一需要你寫的，就是一個 HTTP server，上面兩個 endpoint。\n" +
  "下面那條回路是 trace：你的 agent 把 trace 寫進 Langfuse，系統再讀回來。" +
  "這條回路是後面第三個 level 的前提，等一下會講為什麼 trace id 一定要用我們給的那個。"
);

// ============================================================ 3 — the two endpoints as I/O
s = pres.addSlide();
heading(s, "Two endpoints", "Part 1 · The contract", "Two absolute URLs. Only the first is required.");

const strips = [
  {
    y: 1.85, h: 2.3, fill: PANEL, line: PANEL_2,
    sig: "POST  {chat endpoint}", purpose: "Answer one question.",
    tag: "REQUIRED", tagFill: TEAL, tagColor: WHITE,
    inTitle: "You receive", outTitle: "You return",
    ins: ["one user message", "a trace id to reuse", "a timeout budget", "skill files (sometimes)"],
    outs: ["one text answer"],
    outNote: "An ordinary OpenAI chat completion.",
  },
  {
    y: 4.35, h: 2.05, fill: WHITE, line: PANEL_2,
    sig: "GET  {skills endpoint}", purpose: "List your skill files.",
    tag: "OPTIONAL", tagFill: PANEL_2, tagColor: BODY,
    inTitle: "You receive", outTitle: "You return",
    ins: ["nothing at all"],
    outs: ["{path: file text}", "a version string"],
    outNote: "Flat map. Full text. Never a tree.",
  },
];
strips.forEach((st) => {
  card(s, { x: ML, y: st.y, w: CW, h: st.h, fill: st.fill, line: st.line });
  s.addText(st.sig, {
    x: ML + 0.32, y: st.y + 0.24, w: 3.5, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: M, fontSize: 14, bold: true, color: INK,
  });
  s.addText(st.purpose, {
    x: ML + 0.32, y: st.y + 0.66, w: 3.5, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 14, bold: true, color: TEAL_DK,
  });
  chip(s, {
    x: ML + 0.32, y: st.y + 1.1, w: 1.25, h: 0.3, label: st.tag,
    fill: st.tagFill, line: st.tagFill, color: st.tagColor, fontSize: 9.5, bold: true,
  });

  label(s, { x: 4.85, y: st.y + 0.22, w: 3.0, text: st.inTitle, fontSize: 9.5, color: MUTED });
  st.ins.forEach((t, i) => {
    chip(s, { x: 4.85, y: st.y + 0.56 + i * 0.42, w: 3.0, h: 0.36, label: t, fontSize: 11, fill: WHITE, line: PANEL_2 });
  });

  arrowH(s, { x: 8.05, y: st.y + st.h / 2, w: 0.55, color: TEAL });

  label(s, { x: 8.85, y: st.y + 0.22, w: 3.6, text: st.outTitle, fontSize: 9.5, color: MUTED });
  st.outs.forEach((t, i) => {
    chip(s, { x: 8.85, y: st.y + 0.56 + i * 0.42, w: 3.6, h: 0.36, label: t, fontSize: 11, fill: TEAL_LT, line: TEAL_LN, color: TEAL_DK });
  });
  s.addText(st.outNote, {
    x: 8.85, y: st.y + 0.6 + st.outs.length * 0.42, w: 3.6, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 10.5, italic: true, color: MUTED, align: "center",
  });
});
footnote(s, "No path is imposed: different prefixes, gateways, even different hosts are all fine.");
s.addNotes(
  "約 1.5 分鐘。\n" +
  "只有 chat endpoint 是必須的，而且它就是一個標準的 OpenAI compatible chat completions endpoint——" +
  "single-shot，沒有 conversation、沒有 session、沒有跨呼叫的狀態。" +
  "大部分 agent 前面本來就有一個，所以你今天其實就可以被 evaluate。\n" +
  "它收到的東西：一則 user message（沒有 system message，prompt 是你自己的）、" +
  "一個 trace id、一個 timeout 預算，以及有時候會附上的 skill 檔案。這些額外的東西全部包在一個 top-level key 底下叫 skill_studio——" +
  "為什麼不用 OpenAI 的 metadata？因為 metadata 規格上限是 16 組短字串，skill 檔案塞不進去，嚴格的 gateway 還會直接擋掉。\n" +
  "它要回的東西：一個有內容的文字答案。空字串或只有 tool call 會被算成「這題失敗」，不是「答錯」。\n" +
  "skills endpoint 是選配的，沒有參數，回一個 flat 的「路徑 → 檔案全文」的 map，加一個 version。\n" +
  "另外強調：這是兩個各自獨立的絕對 URL，我們不會幫你組路徑，你放在不同 prefix、不同 gateway、甚至不同 host 都可以。"
);

// ============================================================ 4 — inside one chat call
s = pres.addSlide();
heading(s, "Inside one chat call", "Part 1 · The request");

codeBlock(s, {
  x: ML, y: 1.8, w: 7.5, h: 3.45, fontSize: 11.5, lineSpacing: 16.5,
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
'    "trace_data": {"trace_id": "9f3e11c8...", ...},\n' +
'    "skills": {"billing/SKILL.md": "..."}\n' +
'  }\n' +
'}',
});

label(s, { x: 8.55, y: 1.8, w: 4.1, text: "What your side must do", fontSize: 10, color: TEAL });
[
  ["Reuse the trace id", "not your own"],
  ["Honour timeout_s", "then 504"],
  ["Apply skills, if sent", "those files only"],
  ["Answer with text", "never empty"],
].forEach(([head, tag], i) => {
  const y = 2.2 + i * 0.9;
  card(s, { x: 8.55, y, w: 4.08, h: 0.7, fill: WHITE, line: PANEL_2 });
  token(s, { x: 8.72, y: y + 0.13, w: 0.4, h: 0.4, label: String(i + 1), fontSize: 13 });
  s.addText(head, {
    x: 9.26, y: y + 0.08, w: 3.2, h: 0.26, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 12.5, bold: true, color: INK,
  });
  s.addText(tag, {
    x: 9.26, y: y + 0.35, w: 3.2, h: 0.24, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11, color: MUTED,
  });
  if (i < 3) arrowV(s, { x: 8.92, y: y + 0.7, h: 0.2, width: 1.25, color: PANEL_2 });
});

chip(s, {
  x: ML, y: 5.45, w: 7.5, h: 0.55, fill: TEAL_LT, line: TEAL_LN, color: TEAL_DK, bold: true,
  label: "The trace id is the load-bearing one.", fontSize: 12.5,
});
footnote(s, "The response is an ordinary chat completion. Empty content is a failed question, not a wrong answer — return a 5xx with a reason instead.");
s.addNotes(
  "約 2 分鐘，這是 Part 1 的核心頁。\n" +
  "整個 request 就是標準 OpenAI 格式，唯一的差別是多一個 top-level key: skill_studio。\n" +
  "右邊四件事是你那側要做到的：\n" +
  "1) trace id：這是最 load-bearing 的一項。trace id 是我們先產生再送給你的，你要拿它去寫 Langfuse。" +
  "如果你自己產生一個，chat endpoint 看起來一切正常、evaluation 也會有分數，" +
  "但 step-by-step 檢視、失敗診斷、optimizer 的 reflection 會全部靜悄悄地失效，而且 optimization 根本不會讓你開跑。\n" +
  "2) timeout_s：這是我們給你的預算，請照著做，不要沿用自己寫死的上限（不然把平台的 timeout 調大會沒有效果）。" +
  "但你還是要有自己的天花板，並且到期回 504，不要默默回一個截斷的答案。" +
  "我們送給你的值已經比平台自己等待的時間少 5 秒，所以正常情況下是你先到期，你的錯誤訊息才來得及回到我們這邊。\n" +
  "3) skills：如果這個 key 有出現，就只用這些檔案，不要用你自己硬碟上的。這是下一頁的主題。\n" +
  "4) 答案一定要有文字內容。空字串、只有 tool call、或是 200 帶著一頁 HTML 錯誤頁，都會被算成失敗——" +
  "因為讓 LLM judge 去評一個空字串只會產生一個看起來很有信心的錯誤結論。"
);

// ============================================================ 5 — skills + the override, drawn
s = pres.addSlide();
heading(s, "The skills you run with — and the ones we lend you", "Part 1 · Skills");

codeBlock(s, {
  x: ML, y: 1.8, w: 6.1, h: 1.95, fontSize: 10, lineSpacing: 14,
  text:
'{\n' +
'  "version": "a1b2c3d",\n' +
'  "skills": {\n' +
'    "billing/SKILL.md":              "...",\n' +
'    "billing/references/refunds.md": "...",\n' +
'    "reporting/SKILL.md":            "..."\n' +
'  }\n' +
'}',
});
[
  "Flat map — one key per file",
  "Full text — never truncated",
  "{} is an answer; 5xx is a failure",
  "version moves when behaviour moves",
].forEach((t, i) => {
  chip(s, {
    x: 7.1, y: 1.8 + i * 0.5, w: 5.53, h: 0.42, label: t, align: "left",
    fontSize: 11.5, fill: WHITE, line: PANEL_2,
  });
});

s.addText("Send that same map back on a chat call and it becomes an override — three states:", {
  x: ML, y: 4.05, w: CW, h: 0.3, isTextBox: true, margin: 0, valign: "top",
  fontFace: H, fontSize: 13.5, bold: true, color: INK,
});

const states = [
  {
    key: "no skills key", caption: "Your own files, as normal.",
    files: [["SKILL.md", PANEL_2, BODY], ["refs", PANEL_2, BODY], ["...", PANEL_2, BODY]],
  },
  {
    key: '"skills": { ... }', caption: "These files only, for this call.",
    files: [["SKILL.md", TEAL, WHITE], ["refs", TEAL, WHITE]],
  },
  {
    key: '"skills": { }', caption: "No skill files at all.",
    files: [], empty: true,
  },
];
states.forEach((st, i) => {
  const x = ML + i * 4.04;
  card(s, { x, y: 4.5, w: 3.84, h: 1.95, fill: i === 1 ? TEAL_LT : WHITE, line: i === 1 ? TEAL_LN : PANEL_2 });
  chip(s, {
    x: x + 0.24, y: 4.68, w: 3.36, h: 0.4, label: st.key, mono: true, bold: true,
    fontSize: 11, fill: WHITE, line: PANEL_2, color: TEAL_DK,
  });
  if (st.empty) {
    chip(s, { x: x + 1.16, y: 5.26, w: 1.52, h: 0.4, label: "nothing", dash: "dash", fill: WHITE, line: MUTED, color: MUTED, fontSize: 10.5 });
  } else {
    const w = 1.05, gap = 0.11;
    const total = st.files.length * w + (st.files.length - 1) * gap;
    st.files.forEach(([name, fill, color], k) => {
      chip(s, {
        x: x + (3.84 - total) / 2 + k * (w + gap), y: 5.26, w, h: 0.4,
        label: name, mono: true, fontSize: 8.5, fill, line: fill, color,
      });
    });
  }
  s.addText(st.caption, {
    x: x + 0.24, y: 5.85, w: 3.36, h: 0.4, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: BODY, align: "center",
  });
});
footnote(s, "Replacement, never a patch. Never persisted. Test for the key, not for truthiness — {} is falsy.");
s.addNotes(
  "約 1.5 分鐘。\n" +
  "上半：skills endpoint 回什麼。四個雷區——\n" +
  "不要做成樹狀，一個檔案一個 key，而且 reference 檔案要一起走訪，因為一個 skill 是一個目錄；\n" +
  "不要截斷檔案內容，開發者會在畫面上編輯後送回來，截斷等於幫他刪檔；\n" +
  "空的 {} 是合法答案（代表這個 agent 沒有 skill），但讀不到自己的目錄要回 5xx，這兩件事必須分得開，" +
  "不然開發者會以為自己打的字不見了；\n" +
  "version 只要行為會變就要跟著變——改 skill、換 model、改 prompt、重新部署都算。回一個固定值比不回還糟，" +
  "因為兩個檢查都失效但看起來像有在運作。\n" +
  "下半：同一份 map 出現在 chat call 上就是 override，這是整個 optimization 功能的基礎。三種狀態要分清楚：" +
  "沒有這個 key＝用你自己的；有內容＝只用這些、你硬碟上的其他檔案在這一次呼叫中不存在；" +
  "空的 {}＝這次不要用任何 skill。\n" +
  "程式上要判斷 key 在不在，不要寫 if (skills)，因為 {} 是 falsy，會把「不要用任何 skill」默默變成「用你自己的」。\n" +
  "還有兩件事：這是取代不是合併（合併的話就永遠無法表達「拿掉這個檔案會怎樣」），而且絕對不要寫進你真正的 skills 目錄——" +
  "開發者在 playground 上改一改就會送一次，你的線上 agent 不該被這些影響。"
);

// ============================================================ 6 — three levels
s = pres.addSlide();
heading(s, "You do not have to build all of it", "Part 1 · Three levels",
  "The platform works with what you give it, and names what is missing.");

const levels = [
  {
    n: "1", y: 3.8, h: 2.45, fill: PANEL, line: PANEL_2, head: INK, body: BODY, tok: TEAL,
    build: "A chat endpoint", sub: "what most agents already have",
    unlock: "Evaluation", detail: "Score an eval set. Read every verdict.",
  },
  {
    n: "2", y: 3.15, h: 3.1, fill: TEAL_LT, line: TEAL_LN, head: INK, body: BODY, tok: TEAL,
    build: "+ skills, + override", sub: "one route, one behaviour",
    unlock: "The playground", detail: "Edit skills. Ask one question. Catch drift.",
  },
  {
    n: "3", y: 2.5, h: 3.75, fill: TEAL, line: TEAL, head: WHITE, body: "DDF0F1", tok: TEAL_DK,
    build: "+ our trace id, in Langfuse", sub: "not a third endpoint",
    unlock: "Optimization", detail: "Hundreds of scored rollouts against candidates.",
  },
];
levels.forEach((L, i) => {
  const x = ML + i * 4.04;
  card(s, { x, y: L.y, w: 3.84, h: L.h, fill: L.fill, line: L.line });
  token(s, { x: x + 0.24, y: L.y + 0.22, w: 0.44, h: 0.44, label: L.n, fill: L.tok, fontSize: 15 });
  s.addText(L.build, {
    x: x + 0.82, y: L.y + 0.28, w: 2.8, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 13, bold: true, color: L.head,
  });
  s.addText(L.sub, {
    x: x + 0.24, y: L.y + 0.78, w: 3.36, h: 0.26, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11, italic: true, color: i === 2 ? "BFE7E8" : MUTED,
  });
  label(s, { x: x + 0.24, y: L.y + 1.12, w: 3.36, text: "You get", fontSize: 9.5, color: i === 2 ? MINT : TEAL });
  s.addText(L.unlock, {
    x: x + 0.24, y: L.y + 1.38, w: 3.36, h: 0.36, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 17, bold: true, color: L.head,
  });
  s.addText(L.detail, {
    x: x + 0.24, y: L.y + 1.82, w: 3.36, h: 0.55, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 12, color: L.body,
  });
});
s.addNotes(
  "約 1.5 分鐘，這頁是 Part 1 想讓大家帶走的結論。\n" +
  "不要覺得這個 HTTP server 一定要做到很複雜，它可以分三級，每一級解鎖一塊功能：\n" +
  "Level 1：只有 OpenAI compatible 的 chat endpoint，就能跑 evaluation，也能用到部分 playground。\n" +
  "Level 2：加上 skills endpoint，而且 chat endpoint 要真的套用 skill override，就有完整的 playground——" +
  "可以直接在畫面上改 skill 檔案問一題、有 skill coverage 警告、也會偵測你的 server 在你編輯期間被重新部署過。\n" +
  "Level 3：再加上用我們給的 trace id 把 trace 寫進 Langfuse，就能跑 optimization。\n" +
  "注意 level 3 不是第三個 endpoint，它是 chat endpoint 的兩個行為。系統在 optimization 開跑前會先驗這兩件事：" +
  "它會送一個帶有隱藏 marker 的 skill 過去，然後去 trace 裡面找那個 marker。" +
  "找不到就直接不讓你開跑——與其花一小時得到一條平的曲線，不如當場說清楚。\n" +
  "建議大家就從 level 1 開始，先被 evaluate 起來再說。"
);

// ============================================================ 7 — authentication, drawn
s = pres.addSlide();
heading(s, "If your server needs to know who is asking", "Part 1 · Authentication",
  "Authentication unlocks no feature — but the platform can send a credential.");

const sources = [
  { y: 2.15, head: "A key a developer typed", tag: "one key for the whole agent" },
  { y: 3.85, head: "The signed-in user's token", tag: "SSO, forwarded as that person" },
];
sources.forEach((src, i) => {
  card(s, { x: ML, y: src.y, w: 4.1, h: 1.05, fill: i === 1 ? TEAL_LT : PANEL, line: i === 1 ? TEAL_LN : PANEL_2 });
  s.addText(src.head, {
    x: ML + 0.28, y: src.y + 0.2, w: 3.6, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 13.5, bold: true, color: INK,
  });
  s.addText(src.tag, {
    x: ML + 0.28, y: src.y + 0.55, w: 3.6, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: MUTED,
  });
  // elbow into the shared header pill
  arrowH(s, { x: 4.8, y: src.y + 0.52, w: 0.55, end: false, width: 1.5 });
  arrowV(s, { x: 5.35, y: i === 0 ? src.y + 0.52 : 3.62, h: i === 0 ? 0.85 : 0.75, end: false, width: 1.5 });
});
arrowH(s, { x: 5.35, y: 3.62, w: 0.5, width: 1.5 });

card(s, { x: 5.95, y: 3.2, w: 3.55, h: 0.85, fill: TEAL, line: TEAL });
s.addText("Authorization: Bearer ...", {
  x: 5.95, y: 3.2, w: 3.55, h: 0.85, isTextBox: true, margin: 0,
  fontFace: M, fontSize: 12.5, bold: true, color: WHITE, align: "center", valign: "middle",
});
arrowH(s, { x: 9.6, y: 3.62, w: 0.5, color: TEAL });

card(s, { x: 10.2, y: 3.0, w: 2.43, h: 1.25, fill: INK, line: INK });
s.addText("Your server", {
  x: 10.2, y: 3.28, w: 2.43, h: 0.3, isTextBox: true, margin: 0,
  fontFace: H, fontSize: 14, bold: true, color: WHITE, align: "center", valign: "top",
});
s.addText("one header, never two", {
  x: 10.2, y: 3.62, w: 2.43, h: 0.3, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 11, color: MUTED_LT, align: "center", valign: "top",
});

[
  ["Why the second one matters", "It is the only mode that tells you who is asking, so an agent with its own per-user authorization keeps it."],
  ["Two things to check", "Same realm and same audience. Tokens are re-minted mid-run — cache on the claims, not the token text."],
].forEach(([head, body], i) => {
  const x = ML + i * 6.16;
  card(s, { x, y: 5.35, w: 5.78, h: 1.25, fill: WHITE, line: PANEL_2 });
  s.addText(head, {
    x: x + 0.28, y: 5.52, w: 5.2, h: 0.28, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 12.5, bold: true, color: TEAL_DK,
  });
  s.addText(body, {
    x: x + 0.28, y: 5.84, w: 5.2, h: 0.66, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: BODY,
  });
});
s.addNotes(
  "約 1 分鐘。\n" +
  "先講清楚：你的 server 要不要驗證，系統沒有意見。完全不驗的 server 和一定要驗的 server，在這裡一樣好用。\n" +
  "但如果你需要驗，我們可以送兩種東西過去，而且不管哪一種，你收到的都是同一個 Authorization header：\n" +
  "第一種是開發者自己在 URL 旁邊填的 API key（Bearer，或你指定的 header 名稱例如 X-Api-Key，那種情況不加 Bearer 前綴）。沒填就完全不送這個 header。\n" +
  "第二種是把「當下登入這個平台的那個人」的 SSO token forward 給你。這是重點：" +
  "它是唯一會告訴你「是誰在問」的模式，所以如果你在自己的 agent server 上已經做了 per-user 的權限控制，可以直接接上來，" +
  "不需要為了這個平台開一個共用的後門帳號。\n" +
  "要注意兩件事：一是 realm 和 audience 要對得起來——同 realm 不代表同 aud，aud 要有 audience mapper 才會寫進去，" +
  "如果你嚴格檢查 aud，這是第一個要確認的地方，不然會每一個 request 都失敗；" +
  "二是長時間的 run（eval 幾分鐘、optimization 幾小時）中間 token 會被重新簽發，所以任何 cache 要用 claims 當 key，不要用 token 字串。\n" +
  "兩者可以並存：某個 agent 有自己填的 key 的話，key 優先，你永遠只會收到一個 header。"
);

// ============================================================ 8 — docs + test page (demo cue)
s = pres.addSlide();
heading(s, "You should probably not write this by hand", "Part 1 · Getting there");

[
  ["Open the API reference", "one self-contained page"],
  ["Hand it to your coding assistant", "it writes the server"],
  ["Run Test your server", "it names the level you reached"],
].forEach(([head, tag], i) => {
  const y = 2.0 + i * 1.25;
  token(s, { x: ML, y, w: 0.46, h: 0.46, label: String(i + 1), fontSize: 15 });
  s.addText(head, {
    x: ML + 0.68, y: y - 0.02, w: 5.6, h: 0.34, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 16, bold: true, color: INK,
  });
  s.addText(tag, {
    x: ML + 0.68, y: y + 0.36, w: 5.6, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 12, color: MUTED,
  });
  if (i < 2) arrowV(s, { x: ML + 0.23, y: y + 0.52, h: 0.6, width: 1.25, color: PANEL_2 });
});

// a mock of the documentation page, so the demo has somewhere to land
card(s, { x: ML + 6.9, y: 1.8, w: 5.03, h: 4.3, fill: WHITE, line: PANEL_2 });
s.addShape(pres.ShapeType.roundRect, {
  x: ML + 6.9, y: 1.8, w: 5.03, h: 0.42, rectRadius: 0.06,
  fill: { color: PANEL }, line: { color: PANEL_2, width: 1 },
});
["E06C5E", "E0B65E", "7FBF6A"].forEach((c, i) => {
  s.addShape(pres.ShapeType.ellipse, {
    x: ML + 7.06 + i * 0.22, y: 1.95, w: 0.12, h: 0.12,
    fill: { color: c }, line: { color: c, width: 1 },
  });
});
s.addText("Documentation", {
  x: ML + 7.8, y: 1.84, w: 2.4, h: 0.32, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 10.5, color: MUTED, valign: "middle",
});
card(s, { x: ML + 6.9, y: 2.22, w: 1.85, h: 3.88, fill: "FAFBFC", line: PANEL_2 });
label(s, { x: ML + 7.06, y: 2.4, w: 1.6, text: "Agent server", fontSize: 8.5, charSpacing: 1 });
chip(s, {
  x: ML + 6.98, y: 2.7, w: 1.7, h: 0.34, label: "API reference", align: "left",
  fill: TEAL_LT, line: TEAL_LT, color: TEAL_DK, bold: true, fontSize: 10,
});
s.addText("Test your server", {
  x: ML + 7.08, y: 3.08, w: 1.6, h: 0.34, isTextBox: true, margin: 0,
  fontFace: B, fontSize: 10, color: BODY, valign: "middle",
});
s.addText("Agent Server API", {
  x: ML + 8.95, y: 2.44, w: 2.8, h: 0.3, isTextBox: true, margin: 0, valign: "top",
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
    x: ML + 8.95, y: 2.86 + i * 0.32, w: 2.85, h: 0.28, isTextBox: true, margin: 0,
    fontFace: B, fontSize: 10, color: MUTED, valign: "middle",
  });
});

s.addText("→  DEMO", {
  x: ML, y: 5.95, w: 6.0, h: 0.44, isTextBox: true, margin: 0, valign: "top",
  fontFace: H, fontSize: 17, bold: true, color: TEAL,
});
s.addNotes(
  "約 1 分鐘，講完就切到系統上 demo。\n" +
  "重點：不要自己看著規格從零寫。那份文件是刻意寫成自給自足的——不需要讀我們的 codebase，" +
  "最後還附了一個最小的 reference implementation，和一份可以直接貼進 terminal 跑的 curl acceptance checklist。\n" +
  "所以最快的路徑是：把那一頁餵給你的 coding assistant，讓它幫你把 server 實作出來。\n" +
  "Demo 順序：先開 Agent Server → API reference，捲一下目錄讓大家看到涵蓋的範圍（錯誤碼各自代表什麼、override 的細節、驗收清單）；" +
  "再切到旁邊的 Test your server，說明它會送一個帶著隨機 magic value 的 skill override 過去，" +
  "如果你的回答裡沒有那個值，就代表 override 沒有真的進到你的 model。\n" +
  "最後補一句：文件裡任何讓你需要猜的地方，那都是文件的 bug，直接跟我說。"
);

// ============================================================ 9 — skill selection as a pipeline
s = pres.addSlide();
heading(s, "The same eval loop, pointed at skill selection", "Part 2 · A second use");

const nodes = [
  ["an eval question", WHITE, PANEL_2, BODY],
  ["prefix it", TEAL, TEAL, WHITE],
  ["your agent", INK, INK, WHITE],
  ["the skill it names", WHITE, PANEL_2, BODY],
  ["judged against\nthe skill field", TEAL_LT, TEAL_LN, TEAL_DK],
];
const nw = 2.05, ngap = 0.42;
nodes.forEach(([t, fill, line, color], i) => {
  const x = ML + i * (nw + ngap);
  card(s, { x, y: 1.95, w: nw, h: 0.95, fill, line });
  s.addText(t, {
    x: x + 0.1, y: 1.95, w: nw - 0.2, h: 0.95, isTextBox: true, margin: 0,
    fontFace: H, fontSize: 12, bold: true, color, align: "center", valign: "middle",
  });
  if (i < nodes.length - 1) arrowH(s, { x: x + nw + 0.06, y: 2.42, w: 0.3, color: TEAL });
});

[
  { x: ML, tag: "Answer evaluation", fill: PANEL, line: PANEL_2, tagColor: MUTED,
    q: "What will TSMC's capacity be next year?" },
  { x: ML + 6.16, tag: "Routing evaluation", fill: TEAL_LT, line: TEAL_LN, tagColor: TEAL_DK,
    q: "Which skill would you pick to answer: what will TSMC's capacity be next year?" },
].forEach((c) => {
  card(s, { x: c.x, y: 3.35, w: 5.78, h: 1.4, fill: c.fill, line: c.line });
  label(s, { x: c.x + 0.3, y: 3.55, w: 5.2, text: c.tag, fontSize: 9.5, color: c.tagColor });
  s.addText("“" + c.q + "”", {
    x: c.x + 0.3, y: 3.9, w: 5.2, h: 1.0, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 15, bold: true, color: INK,
  });
});
arrowH(s, { x: 6.36, y: 4.05, w: 0.28, color: TEAL });

[
  ["No new feature", "same upload, run, judge"],
  ["The label already existed", "every question carries a skill field"],
  ["A cheaper signal", "one decision, not a whole trajectory"],
].forEach(([head, tag], i) => {
  const x = ML + i * 4.04;
  card(s, { x, y: 5.4, w: 3.84, h: 1.05, fill: WHITE, line: PANEL_2 });
  s.addText(head, {
    x: x + 0.24, y: 5.56, w: 3.4, h: 0.28, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 12.5, bold: true, color: TEAL_DK,
  });
  s.addText(tag, {
    x: x + 0.24, y: 5.88, w: 3.4, h: 0.4, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: BODY,
  });
});
s.addNotes(
  "約 1 分鐘。\n" +
  "Part 2 的第一件事：這個系統除了評 agent 的回答，我們也拿它來評 agent「選 skill」的能力。\n" +
  "做法很土但有效：把 evaluation question 從「台積電明年產能是多少」改寫成" +
  "「請回答下面這個問題你會選擇哪一個 skill：台積電明年產能是多少」。\n" +
  "這樣現有的 evaluation 功能完全不用改就能用——一樣的上傳格式、一樣的 run、一樣的 judge、一樣的失敗診斷。\n" +
  "而且 ground truth 本來就在：eval set 的每一題本來就有一個 skill 欄位，記著這題「應該」由哪些 skill 回答。\n" +
  "附帶的好處是便宜：agent 在真正開始做事之前就停下來了，一題只需要一個決策，不用跑完整條 trajectory。"
);

// ============================================================ 10 — why routing did not transfer
s = pres.addSlide();
heading(s, "Why the SkillOpt recipe did not transfer", "Part 2 · In flight");

const steps = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(String);
const chartCommon = {
  showTitle: true, titleFontSize: 13, titleColor: INK, titleFontFace: H,
  showLegend: false, showValue: false,
  chartColors: [TEAL],
  lineSize: 2.5, lineSmooth: true,
  catAxisLabelColor: MUTED, catAxisLabelFontSize: 9, catAxisLabelFontFace: B,
  valAxisLabelColor: MUTED, valAxisLabelFontSize: 9, valAxisLabelFontFace: B,
  valAxisMaxVal: 1.1, valAxisMinVal: 0,
  valGridLine: { color: PANEL_2, size: 1 },
  catGridLine: { style: "none" },
  border: { pt: 0, color: WHITE },
};
s.addChart(
  pres.ChartType.line,
  [{ name: "loss", labels: steps, values: [1.0, 0.83, 0.71, 0.62, 0.55, 0.49, 0.45, 0.42, 0.4, 0.39] }],
  Object.assign({}, chartCommon, {
    x: ML, y: 1.85, w: 5.8, h: 2.6,
    title: "A skill body — thousands of words",
  })
);
s.addChart(
  pres.ChartType.line,
  [{ name: "loss", labels: steps, values: [1.0, 0.52, 0.95, 0.38, 0.88, 0.34, 0.93, 0.44, 0.86, 0.4] }],
  Object.assign({}, chartCommon, {
    x: ML + 6.13, y: 1.85, w: 5.8, h: 2.6,
    chartColors: [AMBER],
    lineSmooth: false,
    title: "A description — two or three sentences",
  })
);
s.addText("An edit nudges one section. The loss follows the gradient down.", {
  x: ML, y: 4.5, w: 5.8, h: 0.3, isTextBox: true, margin: 0, valign: "top",
  fontFace: B, fontSize: 12, color: BODY, align: "center",
});
s.addText("Every edit is a rewrite. The loss jumps across the surface.", {
  x: ML + 6.13, y: 4.5, w: 5.8, h: 0.3, isTextBox: true, margin: 0, valign: "top",
  fontFace: B, fontSize: 12, color: AMBER_TX, align: "center",
});

// the fix, as a picture: a stratified batch collapsing into one update
card(s, { x: ML, y: 5.0, w: 9.1, h: 1.6, fill: PANEL, line: PANEL_2 });
label(s, { x: ML + 0.3, y: 5.2, w: 4.0, text: "What we do now", fontSize: 9.5, color: TEAL });
const batchColors = [TEAL, MINT, AMBER];
for (let k = 0; k < 12; k++) {
  s.addShape(pres.ShapeType.roundRect, {
    x: ML + 0.3 + k * 0.36, y: 5.55, w: 0.28, h: 0.28, rectRadius: 0.05,
    fill: { color: batchColors[k % 3] }, line: { color: batchColors[k % 3], width: 1 },
  });
}
s.addText("one big batch, every skill in it", {
  x: ML + 0.3, y: 5.95, w: 4.4, h: 0.3, isTextBox: true, margin: 0, valign: "top",
  fontFace: B, fontSize: 11.5, color: BODY,
});
arrowH(s, { x: 5.45, y: 5.69, w: 0.4, color: TEAL });
chip(s, { x: 5.95, y: 5.48, w: 1.8, h: 0.42, label: "one digest", fill: WHITE, line: PANEL_2, fontSize: 11 });
arrowH(s, { x: 7.85, y: 5.69, w: 0.35, color: TEAL });
chip(s, { x: 8.3, y: 5.48, w: 1.4, h: 0.42, label: "one update", fill: TEAL, line: TEAL, color: WHITE, fontSize: 11, bold: true });
s.addText("no full traces — a confusion matrix and the agent's own setup", {
  x: 5.95, y: 5.95, w: 3.75, h: 0.3, isTextBox: true, margin: 0, valign: "top",
  fontFace: B, fontSize: 11.5, color: MUTED,
});

card(s, { x: ML + 9.4, y: 5.0, w: 2.53, h: 1.6, fill: AMBER_BG, line: AMBER_LN });
token(s, { x: ML + 9.62, y: 5.2, w: 0.4, h: 0.4, label: "!", fill: AMBER, fontSize: 15 });
s.addText("Still\nin flight", {
  x: ML + 10.12, y: 5.2, w: 1.6, h: 0.7, isTextBox: true, margin: 0, valign: "top",
  fontFace: H, fontSize: 13, bold: true, color: AMBER_TX,
});
s.addText("results next sprint", {
  x: ML + 9.62, y: 5.95, w: 2.1, h: 0.3, isTextBox: true, margin: 0, valign: "top",
  fontFace: B, fontSize: 11, color: AMBER_TX,
});
s.addNotes(
  "約 1 分鐘，快速帶過即可（時間不夠可以只講兩張圖然後跳到最後一頁）。\n" +
  "我們原本想直接沿用 SkillOpt 的想法，也就是 isolation mode 那一套，只是把目標換成 skill description。" +
  "結果發現行不通，description 很難收斂，分數一直在動但不會停下來。\n" +
  "後來想清楚原因，就是這兩張圖：optimize 一個 skill body，等於在調一個參數量很大的模型，" +
  "每次改一點（往某個段落補幾句），loss 可以沿著 gradient 慢慢往下走。\n" +
  "但 description 常常只有兩三句話，representation space 很小，改一點就等於整個重寫，" +
  "loss 就在 loss surface 上大幅震盪，收不起來。\n" +
  "所以現在的做法比較樸素：用更大的 batch，加上 stratified sampling 把每個 skill 都平均鋪進每一個 batch，" +
  "算一個比較 global、而且同時考慮所有 skill 的 gradient，避免被某一個 skill 帶偏，也避免小的 skill 明明沒進到這個 batch、description 卻還是被改。\n" +
  "但 batch 一大，optimizer 的 context window 就塞不下每一題的 trace，所以我們不送 trace，改送一個 digest：" +
  "每個 skill 的 confusion matrix（這題被標成誰的、agent 實際打開了誰），加上 agent 自己的 system prompt，" +
  "因為有些 routing 失敗其實是 agent 的 setup 造成的，不是任何一個 description 的錯。\n" +
  "這邊實驗還在跑，還沒有數字，下個 sprint 再跟大家報告。"
);

// ============================================================ 11 — closing
s = pres.addSlide();
s.background = { color: INK };
label(s, { x: ML, y: 1.15, w: CW, text: "What I'd like from you", fontSize: 12, color: MINT, charSpacing: 3 });
s.addText("Start at level 1.", {
  x: ML, y: 1.55, w: 10.5, h: 0.6, isTextBox: true, margin: 0, valign: "top",
  fontFace: H, fontSize: 32, bold: true, color: WHITE,
});

[
  ["Bring the chat endpoint you have", "evaluation needs nothing else"],
  ["Hand the API reference to your assistant", "it was written for that"],
  ["Prove it with Test your server", "it names the level you reached"],
  ["Tell me where the doc made you guess", "that is a bug in the doc"],
].forEach(([head, tag], i) => {
  const x = ML + (i % 2) * 6.16;
  const y = 2.6 + Math.floor(i / 2) * 1.6;
  card(s, { x, y, w: 5.78, h: 1.35, fill: INK_SOFT, line: INK_SOFT });
  token(s, { x: x + 0.28, y: y + 0.26, w: 0.4, h: 0.4, label: String(i + 1), fill: TEAL, fontSize: 14 });
  s.addText(head, {
    x: x + 0.8, y: y + 0.24, w: 4.75, h: 0.44, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 13.5, bold: true, color: WHITE,
  });
  s.addText(tag, {
    x: x + 0.8, y: y + 0.72, w: 4.75, h: 0.4, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: MUTED_LT,
  });
});
s.addText("Questions?", {
  x: ML, y: 6.15, w: CW, h: 0.4, isTextBox: true, margin: 0, valign: "top",
  fontFace: H, fontSize: 17, bold: true, color: MINT,
});
s.addNotes(
  "收尾，約 30 秒。\n" +
  "重申最重要的一句：從 level 1 開始就好，門檻比大家想像的低。" +
  "如果你前面已經有一個 OpenAI compatible 的 endpoint，只要照著文件補上 timeout_s 跟沿用我們的 trace id，這週就可以被 evaluate。\n" +
  "文件是給機器讀的，丟給你的 coding assistant；接好之後用 Test your server 驗一下你到第幾級。\n" +
  "最後拜託：文件裡任何讓你需要猜的地方都跟我說，那是文件的 bug，我修起來最便宜。"
);

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));
