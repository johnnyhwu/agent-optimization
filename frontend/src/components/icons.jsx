import React from "react";

// Lightweight inline SVG icons (no icon-font/library dependency). All inherit
// `currentColor` and take an optional size.
const S = ({ children, size = 16, ...rest }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    {...rest}
  >
    {children}
  </svg>
);

export const IconPlay = (p) => (
  <S {...p}>
    <polygon points="6 4 20 12 6 20 6 4" fill="currentColor" stroke="none" />
  </S>
);
export const IconUpload = (p) => (
  <S {...p}>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </S>
);
export const IconGear = (p) => (
  <S {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </S>
);
export const IconSun = (p) => (
  <S {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </S>
);
export const IconMoon = (p) => (
  <S {...p}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
  </S>
);
export const IconChevronRight = (p) => (
  <S {...p}>
    <polyline points="9 18 15 12 9 6" />
  </S>
);
export const IconArrowLeft = (p) => (
  <S {...p}>
    <line x1="19" y1="12" x2="5" y2="12" />
    <polyline points="12 19 5 12 12 5" />
  </S>
);
export const IconX = (p) => (
  <S {...p}>
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </S>
);
export const IconPlus = (p) => (
  <S {...p}>
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </S>
);
export const IconUsers = (p) => (
  <S {...p}>
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
  </S>
);
export const IconRefresh = (p) => (
  <S {...p}>
    <polyline points="23 4 23 10 17 10" />
    <polyline points="1 20 1 14 7 14" />
    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
  </S>
);
// The record of what a run used — deliberately not IconGear, which means
// *editable* config on the eval-set cards.
export const IconFileText = (p) => (
  <S {...p}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="16" y1="13" x2="8" y2="13" />
    <line x1="16" y1="17" x2="8" y2="17" />
  </S>
);
// Deliberately IconUpload's mirror image: upload and download are the two ends
// of the same round trip, and the cards show both.
export const IconDownload = (p) => (
  <S {...p}>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </S>
);
export const IconTrash = (p) => (
  <S {...p}>
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    <line x1="10" y1="11" x2="10" y2="17" />
    <line x1="14" y1="11" x2="14" y2="17" />
  </S>
);
// Stop, not pause: a cancelled run does not resume.
export const IconStop = (p) => (
  <S {...p}>
    <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />
  </S>
);
export const IconAlert = (p) => (
  <S {...p}>
    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </S>
);
// Send one question to the agent (playground), as distinct from IconPlay, which
// starts a whole eval run.
export const IconSend = (p) => (
  <S {...p}>
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </S>
);
// Duplicate an attempt's settings back into the composer.
export const IconCopy = (p) => (
  <S {...p}>
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </S>
);

/* ---- side-rail section icons ----
   One per top-level section. Each names what the section is *for*, not what it
   contains: measuring against a target, experimenting at the bench, improving
   what came back. */

// Evaluation: scoring answers against ground truth.
export const IconTarget = (p) => (
  <S {...p}>
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
  </S>
);
// Playground: one question at the bench, run as often as you like.
export const IconBeaker = (p) => (
  <S {...p}>
    <path d="M9 3h6" />
    <path d="M10 3v6.5L4.8 18a2 2 0 0 0 1.7 3h11a2 2 0 0 0 1.7-3L14 9.5V3" />
    <path d="M7 15h10" />
  </S>
);
// Shortlist: questions set aside to become an eval set.
export const IconBookmark = (p) => (
  <S {...p}>
    <path d="M6 4h12a1 1 0 0 1 1 1v15l-7-4-7 4V5a1 1 0 0 1 1-1z" />
  </S>
);
// Optimize: the skill comes back better than it went in.
export const IconSparkles = (p) => (
  <S {...p}>
    <path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" />
    <path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
  </S>
);
// Collapse / expand the rail.
export const IconPanelLeft = (p) => (
  <S {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <line x1="9" y1="4" x2="9" y2="20" />
  </S>
);
// The overflow menu's trigger.
export const IconMore = (p) => (
  <S {...p}>
    <circle cx="12" cy="5" r="1.6" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
    <circle cx="12" cy="19" r="1.6" fill="currentColor" stroke="none" />
  </S>
);
export const IconChevronDown = (p) => (
  <S {...p}>
    <polyline points="6 9 12 15 18 9" />
  </S>
);
// Replaces the literal "▲" that used to mark an improved eval set.
export const IconTrendUp = (p) => (
  <S {...p}>
    <polyline points="3 17 9 11 13 15 21 7" />
    <polyline points="15 7 21 7 21 13" />
  </S>
);
// …and the literal "⚠" beside a regressed one. Distinct from IconAlert, which
// means "something failed"; this one means "a number moved the wrong way".
export const IconTrendDown = (p) => (
  <S {...p}>
    <polyline points="3 7 9 13 13 9 21 17" />
    <polyline points="15 17 21 17 21 11" />
  </S>
);
export const IconSearch = (p) => (
  <S {...p}>
    <circle cx="11" cy="11" r="7" />
    <line x1="20" y1="20" x2="16.7" y2="16.7" />
  </S>
);
// Replaces the literal "⏳" on the "the trace hasn't landed yet" banners.
export const IconClock = (p) => (
  <S {...p}>
    <circle cx="12" cy="12" r="9" />
    <polyline points="12 7 12 12 15.5 14" />
  </S>
);
export const IconInfo = (p) => (
  <S {...p}>
    <circle cx="12" cy="12" r="9" />
    <line x1="12" y1="11" x2="12" y2="16.5" />
    <line x1="12" y1="7.6" x2="12" y2="7.7" />
  </S>
);
// Empty states: a shelf with nothing on it.
export const IconInbox = (p) => (
  <S {...p}>
    <path d="M3 13h4l2 3h6l2-3h4" />
    <path d="M5.4 5.5A2 2 0 0 1 7.2 4.5h9.6a2 2 0 0 1 1.8 1l3.4 7.5V18a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5z" />
  </S>
);
