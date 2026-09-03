// Stand-in for src/api.js, used only by the screenshot harness (see
// harness/vite.config.js). Nothing here ships.

const SETS = [
  { id: "11111111-1111-1111-1111-111111111111", name: "Invoices", n_questions: 24 },
  { id: "22222222-2222-2222-2222-222222222222", name: "Contracts", n_questions: 18 },
];

function questions(setIdx, skill, n, start) {
  const set = SETS[setIdx];
  return Array.from({ length: n }, (_, i) => {
    const k = start + i;
    return {
      item_key: `${set.id}:q_${k}`,
      question_id: `q_${k}`,
      question: `Question ${k}: what is the total payable on the ${skill} document, including tax?`,
      ground_truth_response: "42",
      eval_set_id: set.id,
      eval_set_name: set.name,
      skills: [skill],
      prior_accuracy: k % 4 === 0 ? null : (k % 10) / 10,
      prior_runs: k % 3,
    };
  });
}

export const api = {
  optimizationDefaults: async () => ({
    defaults: {
      num_epochs: 3,
      batch_size: 6,
      train_share: 0.7,
      gate_metric: "hard",
      judge_model: "claude-sonnet-5",
      optimizer_model: "claude-opus-5",
      agent_base_url: "http://localhost:9100",
      agent_timeout_s: 120,
      max_reflection_tokens: 4000,
      minibatch_shuffle: true,
      mixed_hard_weight: 0.5,
      accept_margin: 0.0,
      patience: 2,
    },
    system_defaults: {
      num_epochs: 3,
      batch_size: 6,
      gate_metric: "hard",
      judge_model: "claude-sonnet-5",
      optimizer_model: "claude-opus-5",
      agent_base_url: "http://localhost:9100",
      agent_timeout_s: 120,
    },
    judge_prompt: { system: "You are a judge.", user: "Grade this." },
    judge_score_threshold: 0.7,
    limits: { min_train: 8, min_val: 5, warn_train: 20, warn_val: 10 },
    impls: {
      agent: "http", judge: "anthropic", trace: "http",
      workspace: "local", optimizer: "anthropic",
    },
  }),
  listEvalSets: async () => ({ items: SETS.map((s) => ({ ...s, question_count: s.n_questions })) }),
  importPreview: async () => ({
    groups: [
      { skill_name: "invoice-reading", questions: questions(0, "invoice-reading", 14, 1) },
      { skill_name: "contract-review", questions: questions(1, "contract-review", 9, 100) },
    ],
    ambiguous: [],
    sources: SETS.map((s) => ({ ...s, judge_prompt_fingerprint: "abc123" })),
  }),
  skillCheck: async (skill) => ({
    skill_name: skill, exists: true, ok: true, editable: true,
    description: "Reads an invoice and answers questions about it.",
    files: ["SKILL.md"],
  }),
  createOptimizationRun: async () => ({ id: "run-1" }),
};

export const getSubject = () => "alice";
export const apiBase = () => "http://localhost:8000";
export const streamUrl = () => "";
